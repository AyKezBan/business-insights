from fastapi.testclient import TestClient

from insight_agent.api import build_agent,create_app
from insight_agent.config import Settings
from test_material_store import corpus


def test_document_route_and_index_pin_same_startup_snapshot(tmp_path):
    from insight_agent.materials.store import publish_snapshot
    root,old=corpus(tmp_path)
    revision=publish_snapshot(root,old)
    settings=Settings(_env_file=None,materials_dir=root,trace_dir=tmp_path/'traces')
    app=create_app(build_agent(settings))
    root,new=corpus(tmp_path,'A revised rate 18 percent.')
    publish_snapshot(root,new)
    with TestClient(app) as client:
        assert client.get('/health').json()['corpus_revision']==revision
        response=client.get(f'/v1/materials/documents/{old.documents[0].doc_id}/file')
        assert response.status_code==200 and response.headers['content-type']=='application/pdf'
        assert response.content==(root/old.documents[0].relative_path).read_bytes()
        assert client.get(f'/v1/materials/documents/{new.documents[0].doc_id}/file').status_code==404
