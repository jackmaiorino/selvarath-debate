from rejudge import freeze_protocol


def test_render_contains_gates_arms_and_provenance():
    text = freeze_protocol.render()
    for needle in [
        "Δfew ≥ 4", "≤ 2", "3.5", "50%",                      # gates
        "clean", "both", "placebo", "na_only", "doubled_only", "legacy",
        "ORACLE PLACEBO: no factual verification was performed",
        "CLEAN-vs-PLACEBO queries_used distribution parity",
        "parser_version", "K=2",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ]:
        assert needle in text, needle
