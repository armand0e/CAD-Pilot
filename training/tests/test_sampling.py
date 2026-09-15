from cad_policy.sampling import stratified_indices


def test_stratified_selection_balances_apps_and_workflows():
    rows = [{"id": f"{app}-{i}", "software": app, "source": {"workflow_id": str(i % 3)}} for app, n in [("big", 100), ("small", 6)] for i in range(n)]
    keep = stratified_indices(rows, 6, 42)
    assert keep == stratified_indices(rows, 6, 42)
    assert sum(rows[i]["software"] == "small" for i in keep) == 3
    assert len({rows[i]["source"]["workflow_id"] for i in keep if rows[i]["software"] == "big"}) == 3
    assert len(set(keep)) == len(keep)
