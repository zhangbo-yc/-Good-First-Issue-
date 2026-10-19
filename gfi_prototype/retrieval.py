import hashlib
import json
import re
import sqlite3
import stat
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from collect_gfi import materialized_archive_members
from gfi_prototype.common import sha256_file, utc_now

INDEX_VERSION=2
TEXT_SUFFIXES={'.py','.md','.rst','.toml','.yaml','.yml','.ini','.txt'}
EXCLUDED_PARTS={'.git','.agents','.claude','__pycache__','node_modules'}
STOP_WORDS={'this','that','with','from','have','been','will','would','should','could','there','these','issue','first','good','the','and','for','not','are','but','none','true','false','self','https','github','com','blob','main','master','class','name','error','messages'}


def query_terms(text,limit=16):
    preferred=[]
    for value in re.findall(r'(?<!`)`([^`\n]+)`(?!`)',text):
        preferred.extend(re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}',value))
    preferred+=re.findall(r'\b[A-Za-z_]+_[A-Za-z0-9_]+\b|\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b',text)
    preferred+=re.findall(r'[A-Za-z_][A-Za-z0-9_]{3,}',text)
    result=[]; seen=set()
    for term in preferred:
        if term.casefold() in seen or term.casefold() in STOP_WORDS:
            continue
        seen.add(term.casefold()); result.append(term)
        if len(result)==limit:
            break
    return result


class EvidenceStore:
    def __init__(self,folder):
        self.folder=Path(folder)
        self.snapshot=json.loads((self.folder/'source_snapshot.json').read_text(encoding='utf-8'))
        self.repo=self.snapshot['repo']; self.sha=self.snapshot['commit_sha']
        if not re.fullmatch(r'[0-9a-f]{40}',self.sha):
            raise ValueError('Invalid source snapshot SHA')
        expected=f'source-{self.sha}.zip'
        if self.snapshot['archive']!=expected:
            raise ValueError('Source archive filename does not match the pinned SHA')
        self.archive=self.folder/expected
        self.index=self.folder/'retrieval/source_index.sqlite3'

    def build_index(self):
        digest=sha256_file(self.archive)
        if digest!=self.snapshot['archive_sha256']:
            raise ValueError('Source archive checksum mismatch')
        if self.index.exists():
            try:
                with closing(sqlite3.connect(self.index)) as connection:
                    meta=json.loads(connection.execute('SELECT value FROM metadata WHERE key="index"').fetchone()[0])
                if meta['archive_sha256']==digest and meta['index_version']==INDEX_VERSION and meta['repo']==self.repo:
                    return dict(meta,reused=True)
            except (sqlite3.Error,ValueError,KeyError,TypeError):
                pass
        self.index.parent.mkdir(exist_ok=True)
        temporary=self.index.with_suffix('.sqlite3.building')
        connection=sqlite3.connect(temporary)
        file_count=chunk_count=0
        try:
            connection.executescript('DROP TABLE IF EXISTS fts; DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS metadata; '
                'CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); '
                'CREATE TABLE chunks(path TEXT,start_line INTEGER,end_line INTEGER,text TEXT,file_sha256 TEXT,kind TEXT); '
                'CREATE VIRTUAL TABLE fts USING fts5(text,path,content="chunks",content_rowid="rowid");')
            with zipfile.ZipFile(self.archive) as archive:
                for relative,info in materialized_archive_members(archive,
                        preserve_missing_links=self.snapshot.get('missing_internal_link_handling')=='preserve_link_text'):
                    if info is not None and stat.S_ISLNK(info.external_attr >> 16):
                        continue
                    if info is not None and relative!='/'.join(PurePosixPath(info.filename).parts[1:]):
                        # GitHub displays symlink target text at alias paths, not expanded contents.
                        continue
                    path=PurePosixPath(relative)
                    if info is None or path.suffix.lower() not in TEXT_SUFFIXES or info.file_size>1_000_000:
                        continue
                    if set(path.parts)&EXCLUDED_PARTS or path.name.startswith('.env') or path.name.upper() in {'AGENTS.MD','CLAUDE.MD'}:
                        continue
                    raw=archive.read(info)
                    try:
                        text=raw.decode('utf-8')
                    except UnicodeDecodeError:
                        continue
                    if '\x00' in text:
                        continue
                    lines=text.splitlines()
                    if not lines:
                        continue
                    kind='test' if any(p in {'tests','_tests'} for p in path.parts) or path.name.startswith('test_') else 'source' if path.suffix=='.py' else 'document'
                    file_count+=1
                    for first in range(0,len(lines),60):
                        last=min(first+80,len(lines)); excerpt='\n'.join(lines[first:last])
                        cursor=connection.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                            (relative,first+1,last,excerpt,hashlib.sha256(raw).hexdigest(),kind))
                        connection.execute('INSERT INTO fts(rowid,text,path) VALUES(?,?,?)',(cursor.lastrowid,excerpt,relative))
                        chunk_count+=1
            meta={'repo':self.repo,'commit_sha':self.sha,'archive_sha256':digest,'index_version':INDEX_VERSION,
                  'indexed_files':file_count,'indexed_chunks':chunk_count,'built_at':utc_now(),
                  'scope':'UTF-8 text <=1MB in verified source ZIP; excludes agent configs, .env, binary assets and symlink aliases'}
            connection.execute('INSERT INTO metadata VALUES(?,?)',('index',json.dumps(meta)))
            connection.commit()
        finally:
            connection.close()
        temporary.replace(self.index)
        return dict(meta,reused=False)

    def search(self,query,limit=8):
        if not 1<=limit<=16:
            raise ValueError('Source evidence limit must be 1..16')
        terms=query_terms(query)
        if not terms:
            return []
        fts_query=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
        with closing(sqlite3.connect(self.index)) as connection:
            connection.row_factory=sqlite3.Row
            rows=connection.execute('SELECT c.*,bm25(fts) AS rank FROM fts JOIN chunks c ON c.rowid=fts.rowid '
                                    'WHERE fts MATCH ? ORDER BY rank LIMIT 100',(fts_query,)).fetchall()
        selected=[]; per_path={}
        for row in rows:
            if per_path.get(row['path'],0)>=2:
                continue
            selected.append(dict(row)); per_path[row['path']]=per_path.get(row['path'],0)+1
            if len(selected)==limit:
                break
        # Retain a relevant test result when it is available, rather than filling only source chunks.
        if selected and not any(r['kind']=='test' for r in selected):
            test=next((dict(row) for row in rows if row['kind']=='test'),None)
            if test:
                selected[-1]=test
        return selected

    def evidence_bundle(self,number,max_sources=8):
        if type(number) is not int or number<1:
            raise ValueError('Issue number must be a positive integer')
        source_path=self.folder/'issues'/f'{number}.json'
        bundle=json.loads(source_path.read_text(encoding='utf-8'))
        if bundle.get('repo')!=self.repo or not bundle.get('complete') or bundle['issue']['number']!=number:
            raise ValueError('Issue identity/completeness mismatch')
        issue=bundle['issue']; body=issue.get('body') or ''
        evidence=[{'id':'I1','kind':'issue','text':body[:6500],'url':issue['html_url'],
                   'snapshot_at':bundle['fetched_at'],'truncated':len(body)>6500,'bundle_sha256':sha256_file(source_path)}]
        comments=bundle.get('comments',[])[-3:]
        for index,comment in enumerate(comments,1):
            text=comment.get('body') or ''
            evidence.append({'id':f'C{index}','kind':'comment','text':text[:1200],
                             'url':comment['html_url'],'created_at':comment.get('created_at'),
                             'truncated':len(text)>1200,'status':'unverified_comment_claim'})
        query=issue['title']+'\n'+body
        rows=self.search(query,max_sources)
        for index,row in enumerate(rows,1):
            row['id']=f'E{index}'
            row['commit_sha']=self.sha
            row['url']=f'https://github.com/{self.repo}/blob/{self.sha}/{quote(row["path"],safe="/")}#L{row["start_line"]}-L{row["end_line"]}'
            evidence.append(row)
        packet={'repo':self.repo,'issue_number':number,'title':issue['title'],'state_at_snapshot':issue['state'],
                'issue_url':issue['html_url'],'source_commit':self.sha,'source_archive_sha256':self.snapshot['archive_sha256'],
                'snapshot_at':bundle['fetched_at'],'query_terms':query_terms(query),'evidence':evidence,
                'source_evidence_found':len(rows),'current_availability_verified':False,
                'gold_annotations_used':False,'retrieval_method':'SQLite FTS5 over pinned source archive'}
        self.verify_bundle(packet)
        if len(json.dumps(packet,ensure_ascii=False))>75000:
            raise ValueError('Evidence packet exceeds prototype context budget; reduce max_sources')
        return packet

    def verify_bundle(self,packet):
        if packet['repo']!=self.repo or packet['source_commit']!=self.sha:
            raise ValueError('Evidence snapshot identity mismatch')
        if sha256_file(self.archive)!=self.snapshot['archive_sha256']:
            raise ValueError('Evidence archive checksum mismatch')
        with zipfile.ZipFile(self.archive) as archive:
            members={relative:info for relative,info in materialized_archive_members(archive,
                         preserve_missing_links=self.snapshot.get('missing_internal_link_handling')=='preserve_link_text')
                     if info is not None and not stat.S_ISLNK(info.external_attr >> 16)
                     and relative=='/'.join(PurePosixPath(info.filename).parts[1:])}
            for evidence in packet['evidence']:
                if 'path' not in evidence:
                    continue
                info=members.get(evidence['path'])
                if info is None:
                    raise ValueError('Evidence file is missing from pinned archive')
                raw=archive.read(info); lines=raw.decode('utf-8').splitlines()
                start,end=evidence['start_line'],evidence['end_line']
                if not (type(start) is int and type(end) is int and 1<=start<=end<=len(lines)):
                    raise ValueError('Invalid source evidence line range')
                if evidence['file_sha256']!=hashlib.sha256(raw).hexdigest() or evidence['text']!='\n'.join(lines[start-1:end]):
                    raise ValueError('Source evidence text/hash does not match pinned archive')
