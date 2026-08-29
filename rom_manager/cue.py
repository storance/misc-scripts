import re
import pathlib

CUE_FILE_PATTERN = re.compile(r'(FILE\s+")([^"]+\.bin)("\s+BINARY)', re.IGNORECASE)

def list_bin_files_from_cue(cue_file: pathlib.Path) -> list[str]:
    files = []
    with open(cue_file, 'r') as f:
        for line in f:
            match = CUE_FILE_PATTERN.match(line)
            if match is not None:
                files.append(match.group(2))
    return files

def rename_bin_files_in_cue(cue_file: pathlib.Path, file_renames: dict[str, str]):
    def do_rename(match):
        file = match.group(2)
        if file in file_renames:
            return f"{match.group(1)}{file_renames[file]}{match.group(3)}"
        else:
            return match.group(0)

    updated_lines = []
    with open(cue_file, 'r') as f:
        for line in f:
            updated_lines.append(CUE_FILE_PATTERN.sub(do_rename, line))

    with open(cue_file, 'w') as f:
        for line in updated_lines:
            f.write(line)
