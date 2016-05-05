#!/usr/bin/env python
from __future__ import print_function, division

appinfo = """
File extractor for Lisa Monitor disk format.

Works with both "male" (little-endian) and "female" (big-endian) images.
Assumes images are in DC42 format if named .image or .dc42; otherwise
are treated as raw images (works for .po images).

Copyright (C) 2016 David Ryskalczyk
Licensed under the GNU GPL version 2 or any later version.
"""


# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import sys, os, struct, datetime, time, math, argparse, re

def b_to_uint32(dval, little_endian):
    if little_endian:
        return struct.unpack("<I", dval)[0]
    else:
        return struct.unpack(">I", dval)[0]

def b_to_uint16(dval, little_endian):
    if little_endian:
        return struct.unpack("<H", dval)[0]
    else:
        return struct.unpack(">H", dval)[0]

def pascal_string_to_str(dval):
    return struct.unpack("%dp" % len(dval), dval)[0].decode("utf-8")

class FKINDS:
    UNTYPFIL = 0
    XDSKFILE = 1
    CODEFILE = 2
    TEXTFILE = 3
    INFOFILE = 4
    DATAFILE = 5
    GRAFFILE = 6
    FOTOFILE = 7
    SECURDIR = 8
    SEPARATR = -1


def get_type(typ):
    if typ == FKINDS.UNTYPFIL:
        return "UNTYPFIL"
    elif typ == FKINDS.XDSKFILE:
        return "SECURDIR"
    elif typ == FKINDS.CODEFILE:
        return "SECURDIR"
    elif typ == FKINDS.TEXTFILE:
        return "TEXTFILE"
    elif typ == FKINDS.INFOFILE:
        return "DATAFILE"
    elif typ == FKINDS.DATAFILE:
        return "DATAFILE"
    elif typ == FKINDS.GRAFFILE:
        return "DATAFILE"
    elif typ == FKINDS.FOTOFILE:
        return "DATAFILE"
    elif typ == FKINDS.SECURDIR:
        return "SECURDIR"
    elif typ == FKINDS.SEPARATR:
        return "separatr" # lowercase since this is a fake type used to signify a "separator"
    else:
        return "UNKNFILE"

def get_date(dval):
    year = 1900+(dval >> 9)
    month = dval & 0xF
    day = (dval >> 4) & 0x1F
    if(day == 0): day += 1
    return datetime.datetime(year, month, day)
    
def swap_tracks(dval):

    # swap each track on second side.
    ba = bytearray()
    tsecs = 0
    for i in range(46):
        if i >= 0 and i <= 3:
            nsecs = 22
        elif i >= 4 and i <= 10:
            nsecs = 21
        elif i >= 11 and i <= 16:
            nsecs = 20
        elif i >= 17 and i <= 22:
            nsecs = 19
        elif i >= 23 and i <= 28:
            nsecs = 18
        elif i >= 29 and i <= 34:
            nsecs = 17
        elif i >= 35 and i <= 41:
            nsecs = 16
        elif i >= 42 and i <= 45:
            nsecs = 15
        
        # prepend next track to ba
        a = (len(dval)//2) + (tsecs * 0x200)
        b = a + (nsecs * 0x200)
        ba[:0] = dval[a:b]
        tsecs += nsecs
    # prepend first half of the disk to ba
    ba[:0] = dval[0:len(dval)//2]
    return bytes(ba)
    
def clean_null(dval):
    ba = bytearray()
    sz = int(math.ceil(len(dval)//0x200))
    for i in reversed(range(sz)):
        ba[:0] = dval[i*0x200:(i+1)*0x200].rstrip(b'\0')
    return bytes(ba)

def pascal_indent(dval):
    # Pascal style indents are used to save space.
    # These have a Ctrl-P byte (0x10) followed by a byte indicating number of spaces.
    # The byte is the number of spaces to indent + 32, and may be zero.
    ba = bytearray(dval)
    i = 0
    nchars = len(ba)
    while i < nchars:
        if ba[i] == 0x10 and (i!=0 and ba[i-1] == ord('\r')):
            nspac = ba[i+1] - 32
            if nspac < 0:
                print("WARNING: Invalid indent number byte %d!", dval[i+1])
                continue
            del ba[i+1]
            del ba[i]
            nchars -= 2
            for j in range(nspac):
                ba.insert(i+j, ord(' '))
                nchars += 1
        i += 1
    return bytes(ba)

def convert_line_endings(dval, mode):
    if mode == "unix":
        dval = dval.replace(b"\r\n", b"\n")
        dval = dval.replace(b"\r", b"\n")
    elif mode == "mac":
        dval = dval.replace(b"\r\n", b"\r")
        dval = dval.replace(b"\n", b"\r")
    elif mode == "windows":
        dval = re.sub(b"\r(?!\n)|(?<!\r)\n", b"\r\n", dval)
    return dval

## MAIN FUNCTION CODE

def main():
    aparser = argparse.ArgumentParser(description=appinfo, formatter_class=argparse.RawDescriptionHelpFormatter)
    aparser.add_argument('input_image', metavar='input_image.dc42',
                       help='Input file in DiskCopy 4.2 (.image or .dc42 extension) or raw image format')
    aparser.add_argument('output_directory', metavar='outdir',
                       help='Output directory for extracted files')
    aparser.add_argument('-nn', '--no-clean-nulls', action="store_true", help="Don't clean nulls from ends of text file blocks")
    aparser.add_argument('-ni', '--no-fix-indents', action="store_true", help="Don't replace Pascal indents with spaces")
    aparser.add_argument('-le', '--line-endings', choices=["windows", "unix"], help="Convert line endings to Windows [CRLF] or Unix [LF]")
    args = aparser.parse_args()
    # Read in file
    infile = open(args.input_image, "rb")
    dat = bytearray(infile.read())
    infile.close()

    # identify image type
    # DC42 images end with .dc42 or .image and have a header
    if(args.input_image.endswith('.dc42') or args.input_image.endswith('.image')):
        # Extract data section from dc42 image
        datlen = b_to_uint32(dat[0x40:0x44], False)
        datablock = dat[0x54:0x54+datlen]
    # ProDOS order images from an Apple II end with the '.po' file extension and need to be treated as raw images
    else:
        datlen = len(dat)
        datablock = dat

    # Swap if double-sided disk (currently only necessary for fullsize Twiggy disks)
    if(datlen//0x200 >= 0x6A6):
        datablock = swap_tracks(datablock)

    # Read file table and read in files, starts at sector 2.
    # Boot block (sector 0) is skipped, as is sector 1 which appears blank.
    # Format (filename is a Pascal string):
    # 00  01  02  03  04 05 06 07 08 09 10 11 12 13 14 15   16 17   18 19   20 21   22 23   24 25
    # FSTBLK  LSTBLK  FKIND |                       |       |       |       |       |       |
    # FKIND = 0 or 8:       TITLE.................. DEOVBLK DNUMFLS DLOADTM DLASTBT 00 00   00 00
    # FKIND = normal files: TITLE.................................................. LSTBYTE DACCESS

    # All files in the directory contain a first block (FSTBLK), last block (LSTBLK), file kind (FKIND), and title (TITLE, Pascal string).
    # Text files need to have two added to the first block to start in the correct location.
    # The first file is the directory header, and should have type 0 or 8. It contains the volume name (TITLE, pascal string), number of blocks in volume (DEOVBLK), number of files in the directory (DNUMFLS, does NOT include the directory itself), and datestamp (DLASTBT).
    # And additional files in the directory have a size in bytes of the last block (LSTBYTE) and datestamp (DACCESS).
    # LSTBYTE provides information on where the file ends.
    # Text files often have NULLs within the file at the ends of blocks, so these should be cleaned up.
    # The datestamp format has years since 1900 in the first seven bytes, and day of the year (zero indexed) in the remaining nine bytes.
    # IMPORTANT: The second half of the DC42 Twiggy images have the tracks in reverse order. I am uncertain whether this is due to the way the disks were imaged, or due to this file format. They have to be swapped before there is any hope of reading the data. Please see swap_tracks for how this is done.

    # There are "male" and "female" disks. "Male" disks are intended for Apple II and use little-endian numbers; "Female" disks are intended for Lisa and use big-endian numbers.

    files = {}
    f = 0
    nfiles = 1
    dirfound = False
    volname = "."
    disktime = datetime.datetime.now()
    little_endian = False

    while f <= nfiles:
        # read out header
        filedesc = datablock[0x400+(26*f):0x400+(26*(f+1))]
    
        # check if this is a "female" (little endian) disk and handle appropriately
        if f == 0:
            LSTBLK = b_to_uint16(filedesc[2:4], False) # Last block of directory, should always be "6"
            if (LSTBLK & 0xFF) == 0x0:
                little_endian = True

        LSTBLK = b_to_uint16(filedesc[2:4], little_endian) # Last block
        FSTBLK = b_to_uint16(filedesc[0:2], little_endian) # First block (add 2 for FKIND==TEXTFILE)
        FKIND = b_to_uint16(filedesc[4:6], little_endian) # File kind
    
        if (f==0) and (FKIND == FKINDS.UNTYPFIL or FKIND == FKINDS.SECURDIR):
            # This is the directory. Parse it out.
            TITLE = pascal_string_to_str(filedesc[6:14]) # title field
            DEOVBLK = b_to_uint16(filedesc[14:16], little_endian) # end of volume field - number of blocks on disk
            DNUMFLS = b_to_uint16(filedesc[16:18], little_endian) # number of files in directory
            # DLOADTM = b_to_uint16(filedesc[18:20], little_endian) # load time? undocumented, unused, and blank in all images found
            DLASTBT = b_to_uint16(filedesc[20:22], little_endian) # most recent date setting - last modified date
            if(dirfound==False):
                if(DEOVBLK*0x200 != len(datablock)):
                    print("WARNING: Image data length different from length in header! Swapping likely failed!")
                # write disk time as directory timestamp
                try:
                    disktime = get_date(DLASTBT)
                except:
                    print("ERROR: Invalid data in time value! Probably a corrupt disk.")
                    exit(3)
                if(FSTBLK != 26*f):
                    print("ERROR: Header location mismatch from sector location! Probably a corrupt disk.")
                    exit(3)

                nfiles = DNUMFLS
                volname = TITLE.replace('/', ':')
                print("Directory Header:")
                print("FSTBLK\tLSTBLK\tSIZE\tFKIND   \tTITLE   \tDEOVBLK\tDNUMFLS\tDLASTBT")
                print("0x%04X\t0x%04X\t%4d\t%-8s\t%-8s\t0x%04X\t%5d\t%10s" % (FSTBLK, LSTBLK, LSTBLK-FSTBLK, get_type(FKIND), TITLE, DEOVBLK, DNUMFLS, get_date(DLASTBT).strftime('%Y-%m-%d')))
                print("Files:")
                print("FSTBLK\tLSTBLK\tSIZE\tFKIND   \tTITLE           \tLSTBYTE\tDACCESS")
                dirfound = True
            else:
                print("WARNING: Non-directory %s file: %s", (get_type(FKIND), TITLE))
            f += 1
            continue
        elif f==0:
            print("ERROR: Directory not found at beginning of disk!")
            exit(3)
        f += 1
        if dirfound == False:
            continue
  
        TITLE = pascal_string_to_str(filedesc[6:22]) # title field
        LSTBYTE = b_to_uint16(filedesc[22:24], little_endian) # This appears to store the block size for this file
        DACCESS = b_to_uint16(filedesc[24:26], little_endian) # Last access datestamp
        if(FSTBLK == LSTBLK): FKIND = FKINDS.SEPARATR # files with same first and last block are "separators" and used to create directory structure
    # hex or text vals?
        print("0x%04X\t0x%04X\t%4d\t%-8s\t%-16s\t0x%04X\t%10s" % (FSTBLK, LSTBLK, LSTBLK-FSTBLK, get_type(FKIND), TITLE, LSTBYTE, get_date(DACCESS).strftime('%Y-%m-%d')))
    #    print("%6d\t%6d\t%4d\t%-8s\t%-16s\t%5d\t%10s" % (FSTBLK, LSTBLK, LSTBLK-FSTBLK, get_type(FKIND), TITLE, LSTBYTE, get_date(DACCESS).strftime('%Y-%m-%d')))
    
        # get file data
        startoffs = 2 if (FKIND == FKINDS.TEXTFILE) else 0
        #filedat = datablock[(FSTBLK+startoffs)*LSTBYTE:(LSTBLK)*(LSTBYTE)]
        filedat = datablock[(FSTBLK+startoffs)*0x200:((LSTBLK-1)*(0x200))+LSTBYTE]
        # clean up null bytes and fix indentation
        if(FKIND == FKINDS.TEXTFILE):
            if not args.no_clean_nulls:
                filedat = clean_null(filedat)
            if not args.no_fix_indents:
                filedat = pascal_indent(filedat)
            if args.line_endings:
                filedat = convert_line_endings(filedat, args.line_endings)

        # populate dict
        files[f-1] = (TITLE.replace('/', ':'), get_date(DACCESS), filedat, FKIND)

    if dirfound == False:
        print("ERROR: Unable to find directory. Not a Lisa Monitor disk?")
        exit(3)

    # Create dest path if nonexistent
    if not os.path.exists(args.output_directory):
         os.mkdir(args.output_directory)

    # Bail if file exists at this location
    if not os.path.isdir(args.output_directory):
        print("ERROR: File exists at location for disk directory %s!" % args.output_directory)
        exit(2)
    
    basepath = args.output_directory

    # create disk directory (based on filename)
    basepath = os.path.join(basepath, volname)
    if not os.path.exists(basepath):
        os.mkdir(basepath)
    # set timestamp for disk directory
    mtime = time.mktime(disktime.timetuple())
    os.utime(basepath, (mtime,mtime))

    # write files to disk
    curdir = "."
    for f in files:
        opath = "."
        # create directories based on separators if separators are found
        if(files[f][3] == FKINDS.SEPARATR):
            curdir = files[f][0]
            opath = os.path.join(basepath, files[f][0])
            if not os.path.exists(opath):
                os.mkdir(opath)
        else:
            # write out the files!
            opath = os.path.join(basepath, curdir, files[f][0])
            fil = open(opath, 'wb')
            fil.write(files[f][2])
            fil.close()
        # set file datestamp
        mtime = time.mktime(files[f][1].timetuple())
        os.utime(opath, (mtime,mtime))

if __name__ == "__main__":
    main()
