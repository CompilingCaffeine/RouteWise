from routewise.issue_workload import fresh_sheets, split_name, ARRIVAL_FAMILIES


def test_issue_dates_are_explicitly_bound_and_arrival_negatives_are_test_only():
    sheets = fresh_sheets()
    assert len(sheets) == 440 and len({s.id for s in sheets}) == 440
    assert sum(split_name(s) == 'tune' for s in sheets) == 96
    for sheet in sheets:
        if sheet.expected['refund_date'] is not None:
            assert any('refund' in must and 'issued' in must for msg in sheet.messages for must in msg.must)
        if sheet.family in ARRIVAL_FAMILIES:
            assert split_name(sheet) == 'heldout'
        if sheet.family == 'arrival_without_issue':
            assert sheet.expected['refund_date'] is None
            assert any('No refund issue date has been provided' in msg.must for msg in sheet.messages)
