from app.evaluation.golden import run_golden_evaluation


def test_golden_evaluation_records_required_quality_dimensions() -> None:
    summary = run_golden_evaluation()

    assert summary.passed is True
    assert {result.id for result in summary.results} == {
        "python-exact-symbol",
        "react-conceptual-streaming",
        "java-refresh-behavior",
        "weak-evidence-refusal",
    }
    assert all(result.answer_correct for result in summary.results)
    assert all(result.citation_present for result in summary.results)
    assert all(result.citation_relevant for result in summary.results)
    assert all(result.refusal_correct for result in summary.results)
