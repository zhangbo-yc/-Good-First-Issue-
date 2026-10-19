"""Newcomer-oriented descriptions of the five collected projects."""

PROJECTS = {
    'ManimCommunity/manim': {
        'name': 'Manim', 'domain': '可视化', 'icon': 'manim', 'color': '#237a64',
        'tagline': '让数学与代码一起动起来',
        'description': '用 Python 编排几何图形、公式与动画。适合喜欢可视化、希望看到代码变化产生直观效果的贡献者。',
        'topics': ['Python', '数学动画', '图形渲染'],
        'modules': ['图形对象与场景', '几何变换与排版', '动画与渲染', '示例和文档'],
        'background': '能阅读 Python 类与方法，对图形或数学表达感兴趣。',
        'learning': '从图形对象的小行为入手，逐渐理解动画场景与渲染流程。',
    },
    'sympy/sympy': {
        'name': 'SymPy', 'domain': '科学计算', 'icon': 'sympy', 'color': '#42743c',
        'tagline': '用 Python 理解符号与数学',
        'description': '一个纯 Python 符号计算库，处理代数、微积分、方程和表达式。可以从具体数学行为、文档和测试开始贡献。',
        'topics': ['Python', '符号计算', '数学算法'],
        'modules': ['表达式与代数', '方程和微积分', '矩阵与多项式', '公式输出与文档'],
        'background': '具备 Python 基础，愿意结合数学示例阅读测试。',
        'learning': '从一个计算示例追到实现，再用测试说明数学行为的边界。',
    },
    'napari/napari': {
        'name': 'napari', 'domain': '可视化', 'icon': 'napari', 'color': '#7757bf',
        'tagline': '让多维科学图像更易探索',
        'description': '面向科学研究的多维图像查看器，支持图层、交互与插件。可以探索数据如何在桌面界面中被显示和操作。',
        'topics': ['Python', '科学图像', 'Qt 界面'],
        'modules': ['图层与数据模型', '查看器交互', '插件与文件读写', '测试和界面细节'],
        'background': '能够阅读 Python，愿意了解数组、图层或桌面交互。',
        'learning': '围绕一个图层或交互问题，理解数据、事件和界面之间的联系。',
    },
    'dask/dask': {
        'name': 'Dask', 'domain': '数据处理', 'icon': 'dask', 'color': '#b75a26',
        'tagline': '让熟悉的数据操作走向并行',
        'description': '为数组、表格与任务提供并行计算能力。沿着熟悉的 NumPy、pandas 接口，探索分块数据与延迟执行。',
        'topics': ['Python', '数据处理', '并行计算'],
        'modules': ['Array 分块数组', 'DataFrame 数据表', '任务图与调度', '数据读写和文档'],
        'background': '具备 Python 基础，接触过 NumPy 或 pandas 会更容易上手。',
        'learning': '从一个接口行为开始，对照数据结果、任务图与回归测试。',
    },
    'ray-project/ray': {
        'name': 'Ray', 'domain': '分布式', 'icon': 'ray', 'color': '#3972c4',
        'tagline': '把 Python 任务扩展到分布式',
        'description': '面向分布式计算与 AI 应用的工具生态，覆盖任务调度、数据处理、模型训练与服务。可从某个独立模块开始了解。',
        'topics': ['Python', '分布式系统', 'AI 工程'],
        'modules': ['Core 任务与对象', 'Data 数据处理', 'Train 与 Tune', 'Serve 与可观测性'],
        'background': '能阅读 Python 和测试，选择熟悉的子模块逐步进入。',
        'learning': '围绕一个具体模块理解分布式应用中的接口、行为与验证。',
    },
}

TYPE_LABELS = {'bugfix': '修复问题', 'feature': '功能完善', 'documentation': '文档改进',
               'testing': '测试补充', 'refactor': '代码整理', 'performance': '性能优化',
               'maintenance': '工程维护', 'internationalization': '多语言支持',
               'needs_review': '任务探索'}
