"""Compare exported archive inventory with current or diagnostic datasets."""
import argparse
import json
from pathlib import Path
from Source.archive import compare, read_datasets
from Source.workflow import load_config, resolve_path, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,default=Path('Generated/Recovery/archive-inventory.json'))
    parser.add_argument('--datasets',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=resolve_path(load_config()['output'])
    root=args.datasets or output
    archive=json.loads(args.archive.read_text(encoding='utf-8-sig'))
    if archive.get('format')!='endfield-volume-archive' or archive.get('coordinateSpace')!='unity-world':
        raise ValueError('Unsupported archive inventory')
    datasets=read_datasets(root,archive)
    destination=args.output or output/'Recovery/archive-comparison.json'
    write_json(destination,compare(archive,datasets),output)
    print('Archive comparison:',destination)


if __name__=='__main__':
    main()
