"""Create a private handoff package after the existing desktop GIS validation."""
import argparse
from pathlib import Path
import sys
from stage_core import stage, MOBILE
sys.path.insert(0, str(MOBILE / 'app/src/main/python'))
from data_bundle import create

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=MOBILE / 'build/base-campo.zip')
    args = parser.parse_args()
    stage()
    create(MOBILE / 'app/build/generated/core/assets/base', args.output)
    print(args.output)
