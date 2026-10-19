"""PyCharm entry: opt-in JS/TS index and offline diagnostic search. No model API."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from gfi_prototype.common import atomic_json, sha256_file
from gfi_prototype.retrieval import EvidenceStore
from gfi_prototype.research.frontend_retrieval import FrontendEvidenceStore, FRONTEND_SUFFIXES, PROFILE

ROOT=Path(__file__).resolve().parent
REPOS=('ManimCommunity/manim','sympy/sympy','dask/dask','ray-project/ray','napari/napari')


def default_queries(root,repo):
    if repo!='ray-project/ray':
        return [{'kind':'symbol_smoke','query':'refreshInterval'}]
    path=root/'experiments/frozen_20261017/equal_calls_v1/results/ray-project__ray__49011/D.json'
    rows=[]
    if path.exists():
        result=json.loads(path.read_text(encoding='utf-8'))
        for row in result['rounds']:
            rows.extend({'kind':'historical_query_replay','query':q}
                        for q in (row.get('retrieval') or {}).get('queries',[]))
    rows.append({'kind':'symbol_smoke','query':'useSWR refreshInterval'})
    return rows


def inspect(root,repo,queries):
    folder=root/'data'/repo.replace('/','__')
    old=EvidenceStore(folder)
    old_hash=sha256_file(old.index) if old.index.exists() else None
    old_meta=None
    if old.index.exists():
        with closing(sqlite3.connect(old.index.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            old_meta=json.loads(db.execute('SELECT value FROM metadata WHERE key="index"').fetchone()[0])
    print('[1/3] 建立或复用独立JS/TS索引，不改旧索引。',flush=True)
    store=FrontendEvidenceStore(folder)
    meta=store.build_index(on_progress=lambda message:print('  '+message,flush=True))
    print(f'[2/3] 离线检索{len(queries)}个诊断查询，不调用模型。',flush=True)
    rows=[]
    for i,item in enumerate(queries,1):
        before=old.search(item['query'],limit=8) if old_meta is not None else []
        after=store.search(item['query'],limit=8)
        # Reuse archive line/hash validation for every actual returned excerpt.
        store.verify_bundle({'repo':repo,'source_commit':store.sha,'evidence':after})
        frontend=[row['path'] for row in after if Path(row['path']).suffix.lower() in FRONTEND_SUFFIXES]
        rows.append({**item,'legacy_results':before,'frontend_results':after,
                     'frontend_hits':len(frontend),'frontend_paths':frontend})
        print(f'  [{i}/{len(queries)}] {item["kind"]}：新索引前8项中JS/TS片段{len(frontend)}个。',flush=True)
    if (sha256_file(old.index) if old.index.exists() else None)!=old_hash:
        raise ValueError('Legacy index changed during inspection')
    with closing(sqlite3.connect(store.index.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        paths=[row[0] for row in db.execute('SELECT DISTINCT path FROM chunks')]
    return {'profile':PROFILE,'repo':repo,'commit_sha':store.sha,'index_path':str(store.index),
        'legacy_index_sha256':old_hash,'legacy_index_unchanged':True,'legacy_metadata':old_meta,
        'metadata':meta,'frontend_indexed_files':sum(n for suffix,n in meta['files_by_suffix'].items() if suffix in FRONTEND_SUFFIXES),
        'dashboard_frontend_files':sum('/dashboard/client/' in p and Path(p).suffix.lower() in FRONTEND_SUFFIXES for p in paths),
        'queries':rows,'claim_scope':'offline_coverage_diagnostic_only_not_model_quality_or_new_holdout'}


def write_report(value,path):
    meta=value['metadata']
    lines=['# JS/TS源码检索覆盖：改进说明与离线验证','',
        '## 改了什么','',
        '新增可显式选择的`frontend_source_v1`检索配置，在固定归档中索引JS、JSX、TS、TSX。'
        '旧检索器、研究提示词、查询生成、FTS排序、旧实验和前端界面均不改。','',
        '- 原索引：`retrieval/source_index.sqlite3`。',
        '- 新索引：`retrieval/source_index_frontend_v1.sqlite3`。',
        '- 两种索引分开保存；旧运行入口继续走原配置，不会自动切换。',
        '- JS/TS函数和组件标记为源码，`.test.*`、`.spec.*`和`__tests__`下文件标记为测试。',
        '- 新增生成／第三方目录、min/bundle/chunk及哈希资源文件名、生成声明头和超过2000字符长行的过滤。'
        '这些是启发式规则，可能漏掉没有标记的生成物，也可能排除少数手写文件，不能声称完美识别。',
        '- 保留原有UTF-8、1MB文件上限、密钥／代理配置排除、非符号链接来源、路径行号与文件哈希核验。','',
        '## 本次真实索引结果','',
        f'- 项目：`{value["repo"]}`；固定提交：`{value["commit_sha"]}`。',
        f'- 旧索引文件数：{value["legacy_metadata"]["indexed_files"] if value["legacy_metadata"] else "本地没有旧索引，未新建"}。',
        f'- 新索引文件数：{meta["indexed_files"]}；片段数：{meta["indexed_chunks"]}。',
        f'- 纳入JS/TS文件：{value["frontend_indexed_files"]}；其中Dashboard client路径下：{value["dashboard_frontend_files"]}。',
        f'- 旧索引SHA-256前后相同：{value["legacy_index_unchanged"]}。',
        '- 各扩展名文件数：'+json.dumps(meta['files_by_suffix'],ensure_ascii=False)+'。',
        '- 排除统计：'+json.dumps(meta['skipped'],ensure_ascii=False)+'。','',
        '## 查询验证','',
        '默认回放Ray #49011旧D记录中保存的查询，并增加一个固定符号烟测查询。'
        '查询不修改，所有输入都已查看过，仅作事后诊断；不是独立未见测试集，也不代表原D当时执行了每条保存的查询。',
        '各查询使用相同FTS排序及前8片段上限，以下只展示真实命中的JS/TS路径。空结果也保留，不挑掉不理想查询。','']
    for i,row in enumerate(value['queries'],1):
        lines += [f'### 查询{i}：{row["kind"]}', '',f'`{row["query"]}`','',
                  f'前8项中的JS/TS片段：{row["frontend_hits"]}。']
        lines += ['- `'+p+'`' for p in dict.fromkeys(row['frontend_paths'])] or ['- 没有JS/TS命中。']
        lines += ['']
    lines += ['## 如何使用','',
        'PyCharm直接运行根目录`inspect_frontend_retrieval.py`，默认只处理Ray和离线查询。',
        '```powershell','.\\.venv\\Scripts\\python.exe inspect_frontend_retrieval.py','```','',
        '可自定义查询：','```powershell',
        '.\\.venv\\Scripts\\python.exe inspect_frontend_retrieval.py --query "useSWR refreshInterval"','```','',
        '研究代码需主动选择新适配器，旧入口不会受影响：','```python',
        'from gfi_prototype.research.frontend_retrieval import FrontendSnapshotRetriever',
        'retriever = FrontendSnapshotRetriever(root, "ray-project/ray")',
        'packet = retriever.initial(issue_number)',
        '# 若未来调用模型，使用新的实验配置和输出目录，不复用旧答案缓存。','```','',
        '## 可以得出什么结论','',
        '本次修正了JS/TS被白名单直接排除的检索覆盖限制，并验证新返回片段可与固定归档对应。'
        '词法检索仍可能混入后端材料，新增语言不保证每个查询都能找到关键实现。',
        '**没有重新分析Issue，没有调用模型，没有重新评分；不能说D评分已经提高或Ray任务已经解决。**'
        '旧34题结果及其限制保持原样。若之后评价输出质量，应单独版本化并使用明确的开发／测试设计。','']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines),encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',choices=REPOS,default='ray-project/ray')
    parser.add_argument('--query',action='append',help='Repeat for multiple offline queries')
    args=parser.parse_args(argv)
    queries=([{'kind':'user_query','query':q} for q in args.query] if args.query else default_queries(ROOT,args.repo))
    result=inspect(ROOT,args.repo,queries)
    suffix='' if args.repo=='ray-project/ray' else '_'+args.repo.replace('/','__')
    report=ROOT/'output'/f'源码检索覆盖改进_说明与验证{suffix}.md'
    atomic_json(report.with_suffix('.json'),result)
    write_report(result,report)
    print(f'[3/3] 离线验证完成：{report}',flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
