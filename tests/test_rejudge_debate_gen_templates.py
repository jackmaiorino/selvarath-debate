"""Phase-2 seam on debate_gen: supplying final turn templates instead of deriving them.

Stage-1 carries two debater templates and derives the blind opening by stripping the
opponent's position out of the later-turn text. Phase-2 froze four templates instead --
blind-opening and later-turn, honest and dishonest -- each with its own system prompt, so
round-1 blindness is stated rather than derived. Passing a phase-2 template through the
Stage-1 stripping path would corrupt it.

The seam is a selector, defaulting to None so the Stage-1 path is unchanged and the frozen
Stage-1 transcripts still regenerate identically.
"""
from rejudge import config, debate_gen


QUESTION = {
    "id": "CN-001",
    "world": "carath_norn",
    "question": "How many guilds hold council seats?",
    "correct_answer": "Seven guilds hold council seats.",
    "wrong_answer": "Nine guilds hold council seats.",
    "wrong_answer_defensibility": "Two seats were added provisionally.",
}


class Recorder:
    def __init__(self):
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                 **kwargs):
        self.calls.append(messages)
        return "A short debater turn."


def _selector(round_index, is_honest):
    side = "honest" if is_honest else "dishonest"
    phase = "opening" if round_index == 0 else "later"
    return (f"SYSTEM {side} {phase}",
            f"USER {side} {phase}: {{question}} / {{correct_answer}}")


def _generate(client, turn_templates=None):
    return debate_gen.generate_transcript(
        QUESTION, "WORLD DOC", 0, True, config.load_protocol(), client,
        debater_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        protocol_name="uncapped3", turn_templates=turn_templates)


def test_a_selector_supplies_the_system_prompt_for_every_turn():
    client = Recorder()
    _generate(client, turn_templates=_selector)
    systems = [messages[0]["content"] for messages in client.calls]
    assert systems == [
        "SYSTEM honest opening", "SYSTEM dishonest opening",
        "SYSTEM honest later", "SYSTEM dishonest later",
        "SYSTEM honest later", "SYSTEM dishonest later",
    ]


def test_a_selector_supplies_the_user_template_verbatim_without_stripping():
    client = Recorder()
    _generate(client, turn_templates=_selector)
    first_user = client.calls[0][1]["content"]
    assert first_user.startswith("USER honest opening:")
    assert QUESTION["question"] in first_user
    assert QUESTION["correct_answer"] in first_user


def test_omitting_the_selector_leaves_the_stage1_path_unchanged():
    protocol = config.load_protocol()
    with_none = Recorder()
    _generate(with_none, turn_templates=None)
    omitted = Recorder()
    debate_gen.generate_transcript(
        QUESTION, "WORLD DOC", 0, True, protocol, omitted,
        debater_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        protocol_name="uncapped3")
    assert with_none.calls == omitted.calls
    # And it is genuinely the Stage-1 protocol's own text, not a selector's.
    assert with_none.calls[0][0]["content"] == protocol["honest_debater"]["system_prompt"]


def test_the_stage1_path_still_strips_the_opponent_position_from_round_one():
    client = Recorder()
    _generate(client, turn_templates=None)
    opening = client.calls[0][1]["content"]
    later = client.calls[2][1]["content"]
    assert QUESTION["wrong_answer"] not in opening
    assert QUESTION["wrong_answer"] in later
