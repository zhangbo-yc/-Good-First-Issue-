"""Analysis origin identities and their user-facing labels."""

ORIGIN_LABELS = {'real_model_analysis_v2': '真实模型分析',
                 'existing_assistant_task_draft_with_rule_normalization': '助手草稿（规则归一化）'}


def origin_label(origin):
    label = ORIGIN_LABELS.get(origin)
    if label is None:
        raise ValueError(f'Unknown analysis origin: {origin!r}')
    return label
