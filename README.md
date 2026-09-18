# misc-scripts
Repo to contain the collection of python scripts I found useful

## rom-manager
Collapse of several scripts I use to manage my personal rom collections.  There are better and more-generic tools
for doing this, but this seemed like something fun to write.  It's pretty specific to how I like to manage my
rom collection.

### Usage
```
usage: rom-manager.py [-h] [-l LOG_FILE] [-v] [-q] {sync,rename,hash,compress,trim,verify} ...

Useful utilities for managing a ROM collection.

positional arguments:
  {sync,rename,hash,compress,trim,verify}
    sync                Synchronize ROMs between a source and destination location
    rename              Renames ROM files to match the name from a no-intro or redump dat file.
    hash                Creates cached .sha1 files for ROMs in a given directory
    compress            Compresses rom files to a specified format
    trim                Trims or untrims 3ds and NDS rom files
    verify              Verifies roms by comparing their hashes to entries in a no-intro or redump dat file.

options:
  -h, --help            show this help message and exit

Logging Settings:
  -l, --log-file LOG_FILE
                        File to output the logs to.
  -v, --verbose         Enables verbose logging which includes additional details about why or why not actions were taken.
  -q, --quiet           Only prints warnings and errors to the console.
```

#### sync
```
usage: rom-manager.py sync [-h] (-p PROFILE | -f PROFILE_PATH) [-D] [-o {never,size,size-or-time,hash,always}] [-t THREADS] [-d] [-m MODE] [-b BUFFER_SIZE] source destination

positional arguments:
  source                Source directory where the roms exist. Expects a metadata.yml file to exist in this directory that defines the rom folder layout.
  destination           Destination directory where the roms will be copied to.

options:
  -h, --help            show this help message and exit
  -p, --profile PROFILE
                        Use the profile specified by the YAML file in the profiles directory in the source path.
  -f, --profile-path PROFILE_PATH
                        An explicit path to the profile YAML file.
  -D, --dry-run         Runs in dry run mode.
  -o, --overwrite-check {never,size,size-or-time,hash,always}
                        Which check to use when determining if a destination file should be overwritten. Valid choices: never, size, size-or-time, hash, always
  -t, --threads THREADS
                        Number of threads to spawn for copying and hashing files in parallel.
  -d, --delete          Delete files from the destination that are not present in the source.
  -m, --dot-files-mode MODE
                        Control how dot files are handles. Valid choices: ignore, sync-dest, sync-src, sync-both
  -b, --buffer-size BUFFER_SIZE
                        How large of a buffer to use when copying files in kilobytes.
```

#### rename
```
usage: rom-manager.py rename [-h] [-s] [-D] [-t THREADS] [-i] -r ROM_SETS [ROM_SETS ...] input_directory

positional arguments:
  input_directory       The directory where the roms exist. Expects a metadata.yml file to exist in this directory that defines the rom folder layout.

options:
  -h, --help            show this help message and exit
  -s, --sync-group      Synchronizes the rename across all rom sets in the same group.
  -D, --dry-run         Run in dry-run mode.
  -t, --threads THREADS
                        Number of threads to use to hash files in parallel.
  -i, --ignore-cached-hashes
                        Ignore any cached sha1 hashes and force them to be regenerated.
  -r, --rom-sets ROM_SETS [ROM_SETS ...]
                        The name of the rom sets to rename. Note: Include and excludes are ignored on the rom set.
```

#### hash
```
usage: rom-manager.py hash [-h] [-r] -e EXTENSION [EXTENSION ...] [-o] [-t THREADS] input_directory

positional arguments:
  input_directory       Directory to scan and create sha1 hashes.

options:
  -h, --help            show this help message and exit
  -r, --recursive       Recursively hash files in sub directories.
  -e, --extension EXTENSION [EXTENSION ...]
                        Filter to only include the specified file extension.
  -o, --overwrite       Overwrite any existing .sha1 file.
  -t, --threads THREADS
                        Number of threads to use for hashing files.
```
#### compress
```
usage: rom-manager.py compress [-h] [--chdman-path CHDMAN_PATH] [--nkit-path NKIT_PATH] [--xdvdfs-path XDVDFS_PATH] [-f {chd,cso,rvz,ciso,wux,wbfs,xiso}] -r ROM_SET [ROM_SET ...] input_directory

positional arguments:
  input_directory       Directory containing ISO and BIN/CUE files to compress

options:
  -h, --help            show this help message and exit
  --chdman-path CHDMAN_PATH
                        Path to the chdman executable
  --nkit-path NKIT_PATH
                        Path to the nkit V2 cli executable
  --xdvdfs-path XDVDFS_PATH
                        Path to the nkit V2 cli executable
  -f, --format {chd,cso,rvz,ciso,wux,wbfs,xiso}
                        Output format for compressed files. Valid values are chd, cso, rvz, ciso, wux, wbfs, xiso
  -r, --rom-set ROM_SET [ROM_SET ...]
                        Rom sets to compress in the format of input_rom_set[:output_rom_set].If the output rom set is not specified, it will be auto-discovered based on the input rom set's group and the format's
                        extension. Note: Include and excludes are ignored on the rom sets.
```

#### trim
```
usage: rom-manager.py trim [-h] [-u] [-o OUTPUT_DIRECTORY] [-t THREADS] [-b BUFFER_SIZE] [-r] input_path

positional arguments:
  input_path            Input file or directory. If a directory is provided, all .3ds, .cci, and .nds files will be processed.

options:
  -h, --help            show this help message and exit
  -u, --untrim          Untrimms the rom files instead
  -o, --output-directory OUTPUT_DIRECTORY
                        Output directory to write the trimmed/untrimmed files. Defaults to the same directory as input_path.
  -t, --threads THREADS
                        Number of threads to spawn for trimming files in parallel.
  -b, --buffer-size BUFFER_SIZE
                        How large of a buffer to use when copying files in kilobytes.
  -r, --recursive       Recursively search for rom files from the input_path if it's a directory.
```

#### verify
```
usage: rom-manager.py verify [-h] -d DAT_FILE [DAT_FILE ...] -e EXTENSION [EXTENSION ...] [-r] [-t THREADS] input_directory

positional arguments:
  input_directory       Input directory containing the roms to verify.

options:
  -h, --help            show this help message and exit
  -d, --dat-file DAT_FILE [DAT_FILE ...]
                        Location of the dat files containing rom hashes and filenames.
  -e, --extension EXTENSION [EXTENSION ...]
                        Extension of the rom files too look at. For example: iso, chd, cue. For bin/cue files use cue as the extension.
  -r, --recursive       Recursively search sub-directories for rom files to verify.
  -t, --threads THREADS
                        Number of threads to use to hash files in parallel.
```