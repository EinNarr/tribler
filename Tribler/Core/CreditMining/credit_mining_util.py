"""
File containing function used in credit mining module.
"""

import os
from binascii import hexlify, unhexlify

def validate_source_string(source):
    """
    Function to check whether a source string is a valid source or not
    """
    return unhexlify(source) if len(source) == 40 and not source.startswith("http") else source

def source_to_string(source_obj):
    return hexlify(source_obj) if len(source_obj) == 20 and not (source_obj.startswith('http://')
                                                                 or source_obj.startswith('https://')) else source_obj

def string_to_source(source_str):
    # don't need to handle null byte because lazy evaluation
    return source_str.decode('hex') \
        if len(source_str) == 40 and not (os.path.isdir(source_str) or source_str.startswith('http://')\
        or source_str.startswith('https://')) else source_str

def ent2chr(input_str):
    """
    Function to unescape literal string in XML to symbols
    source : http://www.gossamer-threads.com/lists/python/python/177423
    """
    code = input_str.group(1)
    code_int = int(code) if code.isdigit() else int(code[1:], 16)
    return chr(code_int) if code_int < 256 else '?'
