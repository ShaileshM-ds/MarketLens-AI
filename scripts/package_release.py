"""Package source and submission artifacts without dependencies or secrets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
target = root / 'release' / 'MarketLens-AI-source.zip'
target.parent.mkdir(exist_ok=True)
excluded = {'sources', '.git', '.runtime', '.venv', 'node_modules', 'dist',
            '__pycache__', '.pytest_cache', 'test-results', 'playwright-report',
            'tmp', 'release', '.codex', '.agents'}
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if not path.is_file() or any(part in excluded for part in rel.parts):
            continue
        if path.name == '.env' or path.suffix in {'.log', '.pyc', '.tsbuildinfo'}:
            continue
        archive.write(path, rel.as_posix())
with ZipFile(target) as archive:
    assert archive.testzip() is None
    print(f'{target}: {len(archive.namelist())} files, {target.stat().st_size:,} bytes')
