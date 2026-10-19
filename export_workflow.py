"""Run in PyCharm to export the actual LangGraph topology, without API calls."""
from pathlib import Path

from gfi_prototype.research.pipeline import PIPELINE_VERSION, build_workflow


def main():
    # Graph compilation/inspection does not invoke nodes or instantiate a model.
    graph = build_workflow(retriever=None, session=None)
    mermaid = graph.get_graph().draw_mermaid()
    output = Path(__file__).resolve().parent / 'output'
    output.mkdir(exist_ok=True)
    diagram = output / 'langgraph_workflow.mmd'
    diagram.write_text(mermaid, encoding='utf-8')
    page = output / 'langgraph_workflow.md'
    page.write_text('# Agent 工作流\n\n版本：`' + PIPELINE_VERSION + '`\n\n```mermaid\n' + mermaid
                    + '\n```\n\n节点：analyze（任务分析）、audit（证据核查）、gate（结论门控）、'
                    'supplement（证据补检）、finalize（结果输出）。\n'
                    '\nA/B在分析后直接输出；C核查和门控后输出；D按缺口补检并重新分析，最多补检2轮。\n',
                    encoding='utf-8')
    print(f'工作流版本：{PIPELINE_VERSION}')
    print('已编译真实LangGraph；未调用大模型，不消耗API Token。')
    print(mermaid)
    print(f'图文件：{diagram}\n阅读版：{page}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
