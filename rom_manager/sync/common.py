from enum import StrEnum

class HashFileSource(StrEnum):
    SOURCE_FILE = 'src'
    DEST_FILE = 'dst'


class OverwriteCheck(StrEnum):
    NEVER = 'never'
    SIZE = 'size'
    SIZE_OR_TIME = "size-or-time"
    HASH = 'hash'
    ALWAYS = 'always'


class DotFilesMode(StrEnum):
    IGNORE = "ignore"
    SYNC_DEST = "sync-dest"
    SYNC_SRC = "sync-src"
    SYNC_BOTH = "sync-both"

    def should_delete(self):
        return self == DotFilesMode.SYNC_BOTH or self == DotFilesMode.SYNC_DEST

    def should_copy(self):
        return self == DotFilesMode.SYNC_BOTH or self == DotFilesMode.SYNC_SRC
