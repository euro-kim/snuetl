"""Create a versioned optional component and pin its hash into the core payload."""
import hashlib,json,sys,zipfile
from pathlib import Path
source,archive,version,manifest = sys.argv[1:]
source,archive,manifest = Path(source),Path(archive),Path(manifest)
files = sorted(p for p in source.rglob('*') if p.is_file())
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
    for file in files: output.write(file,file.relative_to(source).as_posix())
with archive.open('rb') as stream: checksum=hashlib.file_digest(stream,'sha256').hexdigest()
descriptor=dict(Version=version,Url=f'https://github.com/euro-kim/snuetl/releases/download/v{version}/{archive.name}',Sha256=checksum,DownloadBytes=archive.stat().st_size,InstalledBytes=sum(f.stat().st_size for f in files))
manifest.write_text(json.dumps(descriptor,indent=2),encoding='utf8')
archive.with_suffix('.json').write_text(json.dumps(descriptor,indent=2),encoding='utf8')
print(f'Optional sign-in: {archive.stat().st_size / 1048576:.1f} MB download')
