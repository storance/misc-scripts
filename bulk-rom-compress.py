import argparse
import os
import sys
import pathlib
import subprocess

OUTPUT_FORMATS = ('chd', 'cso', 'nkit', 'rvz')

def main():


    parser = argparse.ArgumentParser(description='Compress CHD files in bulk.')
    parser.add_argument('--chdman-path', default='chdman.exe', help='Path to the chdman executable (default: chdman in PATH)')
    parser.add_argument('--maxcso-path', default='maxcso.exe', help='Path to the maxcso executable (default: maxcso in PATH)')
    parser.add_argument('--dolphintool-path', default='Dolphin-x64\\DolphinTool.exe', help='Path to the dolphintool executable (default: dolphintool in PATH)')
    parser.add_argument('--nkit-iso-path', default='NKit\\ConvertToNKit.exe', help='Path to the nkit executable (default: nkit in PATH)')
    parser.add_argument('-f', '--format', choices=OUTPUT_FORMATS, default='chd', help='Output format for compressed files (default: chd)')

    parser.add_argument('-o', '--output-directory', default=None, help='Output directory for compressed CHD files (default: CHD path relative to input directory)')
    parser.add_argument('input_directory', help='Directory containing ISO and BIN/CUE files to compress')
    args = parser.parse_args()

    input_directory = pathlib.Path(args.input_directory)

    if not input_directory.exists() or not input_directory.is_dir():
        print(f"Error: {args.input_directory} is not a valid directory.", file=sys.stderr)
        return 1

    if args.output_directory is None:
        if args.format == 'chd':
            output_directory = input_directory / 'CHD'
        elif args.format == 'cso':
            output_directory = input_directory / 'CSO'
        elif args.format == 'nkit':
            output_directory = input_directory / 'NKIT'
        elif args.format == 'rvz':
            output_directory = input_directory / 'RVZ'
        else:
            print(f"Error: Output directory not specified for format {args.format}.", file=sys.stderr)
            return 1

        if not output_directory.exists():
            output_directory.mkdir()
    else:
        output_directory = pathlib.Path(args.output_directory)

        if not output_directory.exists():
            print(f"Error: {args.output_directory} is not a valid directory.", file=sys.stderr)
            return 1

    for child in input_directory.iterdir():
        if not child.is_file():
            continue

        if args.format == 'chd' and child.suffix == '.iso':
            compress_iso_to_chd(args.chdman_path, child, output_directory)
        elif args.format == 'chd' and child.suffix == '.cue':
            compress_cue_to_chd(args.chdman_path, child, output_directory)
        elif args.format == 'cso' and child.suffix == '.iso':
            compress_iso_to_cso(args.maxcso_path, child, output_directory)
        elif args.format == 'nkit' and child.suffix == '.iso':
            compress_iso_to_nkit(args.nkit_iso_path, child, output_directory)
        elif args.format == 'rvz' and child.suffix == '.iso':
            compress_iso_to_rvz(args.dolphintool_path, child, output_directory)

    return 0


def compress_iso_to_chd(chdman_path, iso_path, output_directory):
    output_file = output_directory / (iso_path.with_suffix('.chd').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: chd output file already exists.")
        return

    try:
        print (f"Compressing {iso_path.name} to {output_file}...")
        print(("=" * 34) + "CHDMAN OUTPUT" + ("=" * 33))
        subprocess.run([chdman_path, 'createdvd', '-i', str(iso_path), '-o', str(output_file)], check=True)
        print("=" * 80)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}: {e}", file=sys.stderr)

def compress_cue_to_chd(chdman_path, cue_path, output_directory):
    output_file = output_directory / (cue_path.with_suffix('.chd').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: chd output file already exists.")
        return
    
    try:
        print (f"Compressing {cue_path.name} to {output_file}...")
        print(("=" * 34) + "CHDMAN OUTPUT" + ("=" * 33))
        subprocess.run([chdman_path, 'createcd', '-i', str(cue_path), '-o', str(output_file)], check=True)
        print("=" * 80)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {cue_path.name}: {e}", file=sys.stderr)

def compress_iso_to_cso(maxcso_path, iso_path, output_directory):
    output_file = output_directory / (iso_path.with_suffix('.cso').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: cso output file already exists.")
        return
    
    try:
        print (f"Compressing {iso_path.name} to {output_file}...")
        print(("=" * 34) + "MAXCSO OUTPUT" + ("=" * 33))
        subprocess.run([maxcso_path, str(iso_path), '-o', str(output_file)], check=True)
        print("=" * 80)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}: {e}", file=sys.stderr)

def compress_iso_to_nkit(nkit_path, iso_path, output_directory):
    output_name = iso_path.with_suffix('.nkit.iso').name
    output_file = output_directory / output_name
    if output_file.exists():
        print(f"Skipping {output_file.name}: nkit output file already exists.")
        return

    try:
        print (f"Compressing {iso_path.name} to {output_file}...")
        print(("=" * 35) + "NKIT OUTPUT" + ("=" * 34))
        subprocess.run([nkit_path, str(iso_path)], input='\n', text=True, check=False)
        print("=" * 80)

        wii_output_path = pathlib.Path(nkit_path).parent / 'Processed' / 'Wii' / output_name
        if wii_output_path.exists():
            wii_output_path.rename(output_file)
            return

        gamecube_output_path = pathlib.Path(nkit_path).parent / 'Processed' / 'GameCube' / output_name
        if gamecube_output_path.exists():
            gamecube_output_path.rename(output_file)
            return

        print(f"Error: Failed to find the output file for {iso_path.name} after nkit compression.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}: {e}", file=sys.stderr)

def compress_iso_to_rvz(dolphintool_path, iso_path, output_directory):
    output_file = output_directory / (iso_path.with_suffix('.rvz').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: rvz output file already exists.")
        return

    try:
        print (f"Compressing {iso_path.name} to {output_file}...")
        subprocess.run([dolphintool_path, 'convert', '-i', str(iso_path), '-o', str(output_file), '-f', 'rvz', '-b', '131072', '-c', 'zstd', '-l', '5'], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}: {e}", file=sys.stderr)
    



if __name__ == '__main__':
    sys.exit(main())