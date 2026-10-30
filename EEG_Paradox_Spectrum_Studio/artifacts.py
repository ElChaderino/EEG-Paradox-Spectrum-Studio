"""Validate retained local artifact inventories before restoring or updating."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .intake import sha256


def read_object(path,label):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Invalid '+label+': expected a JSON object')
    return value


def verify_inventory(root,files,*,required=(),flat=False,changed='Run artifact changed since generation'):
    """Hash files in bounded chunks, with all paths confined to their export."""
    root=Path(root).resolve()
    if not isinstance(files,dict) or not files:raise ValueError('Invalid artifact inventory: no retained files')
    for name in required:
        if not isinstance(name,str) or name not in files:
            raise ValueError('Artifact inventory is missing a required file')
    paths=[]
    for name,digest in files.items():
        if (not isinstance(name,str) or not name or '\\' in name or Path(name).anchor
                or any(part in ('','.','..') for part in name.split('/'))
                or not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None):
            raise ValueError('Invalid artifact inventory path or SHA-256 digest')
        path=(root/name).resolve()
        if not path.is_relative_to(root) or (flat and path.parent!=root):
            raise ValueError('Artifact inventory path lies outside its export folder')
        paths.append((name,path,digest))
    for name,path,digest in paths:
        if not path.is_file() or sha256(path)!=digest:raise ValueError(changed+': '+name)
    return files
