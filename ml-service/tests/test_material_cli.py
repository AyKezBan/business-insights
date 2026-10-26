import json
from datetime import datetime,timezone

from insight_agent.materials.models import RawNews


def saved(tmp_path):
    path=tmp_path/'news.jsonl'
    record=RawNews(url='https://www.cbr.ru/press/example',title='Rate decision',text='Rate remains 14%.',
        published_at=datetime(2026,9,11,tzinfo=timezone.utc),retrieved_at=datetime(2026,9,11,tzinfo=timezone.utc))
    path.write_text(record.model_dump_json()+'\n')
    return path


def test_cli_saved_news_repeat_check_and_corruption(tmp_path,capsys):
    from insight_agent.materials.cli import main
    from insight_agent.materials.store import load_snapshot
    path=saved(tmp_path);root=tmp_path/'store'
    args=['news','--saved',str(path),'--store',str(root)]
    assert main(args)==0
    revision=load_snapshot(root).revision
    assert main(args)==0 and load_snapshot(root).revision==revision
    assert main(['check','--store',str(root)])==0
    (root/'snapshots'/revision/'news.jsonl').write_text('bad')
    assert main(['check','--store',str(root)])==1


def test_cli_failed_source_preserves_old_snapshot(tmp_path):
    from insight_agent.materials.cli import main
    from insight_agent.materials.store import load_snapshot
    root=tmp_path/'store'
    assert main(['news','--saved',str(saved(tmp_path)),'--store',str(root)])==0
    old=load_snapshot(root).revision
    assert main(['news','--saved',str(tmp_path/'missing'),'--store',str(root)])==1
    assert load_snapshot(root).revision==old


def test_cli_bank_review_gate_keeps_news_and_previous_pointer(tmp_path,monkeypatch):
    from insight_agent.materials import cli
    from insight_agent.materials.store import load_snapshot
    from test_material_store import corpus
    root=tmp_path/'store'
    assert cli.main(['news','--saved',str(saved(tmp_path)),'--store',str(root)])==0
    old=load_snapshot(root).revision
    (tmp_path/'bank-test').mkdir()
    _,bank=corpus(tmp_path/'bank-test')
    # Move immutable artifacts into the CLI store just as prepare_bank would do.
    import shutil
    shutil.copytree(tmp_path/'bank-test'/'store',root,dirs_exist_ok=True)
    monkeypatch.setattr(cli,'prepare_bank',lambda *a,**kw:bank)
    args=['bank','--pdf-dir','input','--manifest','manifest','--store',str(root)]
    assert cli.main(args)==2 and load_snapshot(root).revision==old
    bank.bank_fragments[0].review_status='verified'
    bank.bank_fragments[0].statement_type='bank_assessment'
    from datetime import date
    bank.bank_fragments[0].doc_date=date(2026,9,11)
    assert cli.main(args)==0
    snapshot=load_snapshot(root)
    assert len(snapshot.news)==1 and len(snapshot.bank_fragments)==1
