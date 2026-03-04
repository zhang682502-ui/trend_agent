from core.voice_tuner import _pick_best_result, build_candidate_settings


def test_pick_best_result_prefers_consensus_text_over_fast_divergent_result():
    results = [
        {
            "success": True,
            "normalized_text": "status report",
            "beam_size": 5,
            "wall_time_seconds": 2.2,
        },
        {
            "success": True,
            "normalized_text": "status report",
            "beam_size": 1,
            "wall_time_seconds": 1.9,
        },
        {
            "success": True,
            "normalized_text": "status support",
            "beam_size": 1,
            "wall_time_seconds": 1.1,
        },
    ]

    selected = _pick_best_result(results)

    assert selected is not None
    assert selected["normalized_text"] == "status report"


def test_build_candidate_settings_always_includes_cpu_defaults():
    candidates = build_candidate_settings({"cuda_usable": False, "cuda_usable_compute_types": []})

    assert candidates
    assert any(candidate["device"] == "cpu" for candidate in candidates)
    assert any(candidate["beam_size"] == 5 for candidate in candidates)
    assert len({tuple(sorted(candidate.items())) for candidate in candidates}) == len(candidates)
