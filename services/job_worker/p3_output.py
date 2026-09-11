"""Pinned private output transport; canonical SQL remains the only job authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.alpha_lifecycle.sandbox_policy import CHILD_POLICY, MAX_ATTEMPT_OUTPUT_BYTES

_MAX_FILES = 8192
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _identity(info):
    return info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns


class P3OutputCustody:
    def __init__(self, parent_fd: int, output_fd: int, name: str, store: LocalArtifactStore):
        if type(store) is not LocalArtifactStore or re.fullmatch('[0-9a-f]{64}',name) is None:
            raise ValueError('P3 output custody requires the retained store and exact attempt directory')
        self._parent = self._output = -1
        self._retained = None
        self._name,self._store = name,store
        self._identity = (os.fstat(output_fd).st_dev,os.fstat(output_fd).st_ino)
        try:
            self._parent = os.dup(parent_fd)
            self._output = os.dup(output_fd)
            for fd in (self._parent,self._output):
                info=os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError('P3 output directories must be private and owned')
                os.set_inheritable(fd,False)
            self._check()
        except BaseException:
            self._close()
            raise

    def matches(self, job_id: str, attempt_id: str) -> bool:
        return self._name == hashlib.sha256(f'{job_id}/{attempt_id}'.encode()).hexdigest()

    def _check(self):
        if self._parent < 0 or self._output < 0:
            raise ValueError('P3 output custody is closed')
        current=os.stat(self._name,dir_fd=self._parent,follow_symlinks=False)
        opened=os.fstat(self._output)
        if (not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino) != self._identity
            or any(info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700 for info in (current,opened))):
            raise ValueError('P3 output directory identity changed')

    def _close(self):
        if self._retained is not None:
            directories=self._retained[0]
            self._retained=None
            for relative,fd in directories.items():
                if relative:
                    os.close(fd)
        for name in ('_output','_parent'):
            fd=getattr(self,name)
            setattr(self,name,-1)
            if fd >= 0:
                os.close(fd)

    def abandon(self):
        try:
            if self._parent >= 0:
                self._check()
                if not os.listdir(self._output):
                    os.rmdir(self._name,dir_fd=self._parent)
        except (OSError,ValueError):
            # Preserve nonempty or replaced paths for evidence/recovery; never infer job state from them.
            pass
        finally:
            self._close()

    @staticmethod
    def _read(directory, name, expected):
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=directory)
        try:
            before=os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) not in {0o600,0o644}
                or not 0 < before.st_size <= CHILD_POLICY['child_output_bytes']
                or _identity(before) != expected):
                raise ValueError('P3 output file identity or bound differs')
            with os.fdopen(fd,'rb',closefd=False) as stream:
                raw=stream.read(before.st_size+1)
            if len(raw) != before.st_size or _identity(os.fstat(fd)) != expected:
                raise ValueError('P3 output changed during readback')
            if canonical_json_bytes(json.loads(raw)) != raw:
                raise ValueError('P3 output must be canonical JSON')
            digest=hashlib.sha256(raw).hexdigest()
            if name.endswith('.blob') and name != digest+'.blob':
                raise ValueError('P3 private CAS digest differs')
            return raw
        finally:
            os.close(fd)

    def retain(self) -> ArtifactRefV1:
        if self._retained is not None:
            raise ValueError('P3 output was already retained')
        self._check()
        directories={'':self._output}
        listing={}
        files=[]
        total=0
        try:
            for relative in ('','artifacts','r1','r2','r3','r1/artifacts','r2/artifacts','r3/artifacts'):
                if relative:
                    parent,_,name=relative.rpartition('/')
                    if parent not in directories or name not in listing[parent]:
                        continue
                    fd=os.open(name,_FLAGS,dir_fd=directories[parent])
                    directories[relative]=fd
                    info=os.fstat(fd)
                    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                        raise ValueError('P3 output subdirectory is unsafe')
                fd=directories[relative]
                names=sorted(os.listdir(fd))
                listing[relative]=names
                if len(names)+len(files) > _MAX_FILES:
                    raise ValueError('P3 output inventory exceeds its bound')
                allowed_dirs={'artifacts','r1','r2','r3'} if not relative else ({'artifacts'} if relative in {'r1','r2','r3'} else set())
                for name in names:
                    if name in allowed_dirs:
                        continue
                    if (relative.endswith('artifacts') and re.fullmatch('[0-9a-f]{64}\\.blob',name)
                        or relative in {'r1','r2','r3'} and name in {'manifest-ref.json','result.json'}):
                        info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                        total+=info.st_size+len(name)
                        if total > MAX_ATTEMPT_OUTPUT_BYTES:
                            raise ValueError('P3 attempt output exceeds its total bound')
                        expected=_identity(info)
                        raw=self._read(fd,name,expected)
                        ref=ArtifactRefV1(content_sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),
                            media_type='application/json',locator=hashlib.sha256(raw).hexdigest()+'.blob')
                        files.append((relative,name,expected,ref))
                    else:
                        raise ValueError('P3 output contains an unexpected path')
            inventory=[]
            for relative,name,expected,ref in files:
                raw=self._read(directories[relative],name,expected)
                if self._store.put_bytes(raw,media_type=ref.media_type) != ref or self._store.read_bytes(ref) != raw:
                    raise ValueError('P3 retained output readback differs')
                inventory.append(dict(path=relative+'/'+name,artifact_ref=ref))
            inventory_raw=canonical_json_bytes(inventory)
            inventory_ref=self._store.put_bytes(inventory_raw,media_type='application/json')
            if self._store.read_bytes(inventory_ref) != inventory_raw:
                raise ValueError('P3 retained output inventory differs')
            self._retained=(directories,listing,files,inventory_ref)
            return inventory_ref
        except BaseException:
            for relative,fd in directories.items():
                if relative:
                    os.close(fd)
            self._close()
            raise

    @property
    def inventory_ref(self) -> ArtifactRefV1:
        if self._retained is None:
            raise ValueError('P3 output inventory is unavailable')
        return self._retained[3]

    @property
    def inventory(self):
        if self._retained is None:
            raise ValueError('P3 output inventory is unavailable')
        return {relative+'/'+name:ref for relative,name,_,ref in self._retained[2]}

    def cleanup(self):
        if self._retained is None:
            raise ValueError('P3 outputs must be retained before cleanup')
        directories,listing,files,_=self._retained
        try:
            self._check()
            if any(sorted(os.listdir(fd)) != listing[relative] for relative,fd in directories.items()):
                raise ValueError('P3 output inventory changed before cleanup')
            for relative,name,expected,_ in files:
                if _identity(os.stat(name,dir_fd=directories[relative],follow_symlinks=False)) != expected:
                    raise ValueError('P3 output file changed before cleanup')
                os.unlink(name,dir_fd=directories[relative])
            for relative in sorted((r for r in directories if r),key=lambda r:(r.count('/'),r),reverse=True):
                parent,_,name=relative.rpartition('/')
                current=os.stat(name,dir_fd=directories[parent],follow_symlinks=False)
                opened=os.fstat(directories[relative])
                if (current.st_dev,current.st_ino) != (opened.st_dev,opened.st_ino):
                    raise ValueError('P3 output subdirectory changed before cleanup')
                os.rmdir(name,dir_fd=directories[parent])
            self._check()
            os.rmdir(self._name,dir_fd=self._parent)
        finally:
            self._close()

    def retain_and_cleanup(self) -> ArtifactRefV1:
        reference=self.retain()
        self.cleanup()
        return reference
