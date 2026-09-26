import json
import logging
from app.observability import JsonFormatter, identity_var, request_id_var


def test_request_id_validation_and_error_responses(client):
    assert client.get('/health/live', headers={'X-Request-ID': 'safe_123-test'}).headers['X-Request-ID'] == 'safe_123-test'
    for value in ['has spaces', 'x'*65, '<script>']:
        response = client.get('/missing', headers={'X-Request-ID': value})
        assert response.status_code == 404
        assert len(response.headers['X-Request-ID']) == 32
        assert response.headers['X-Request-ID'] != value


def test_metrics_requires_explicit_token_and_uses_route_templates(client, monkeypatch):
    monkeypatch.setenv('METRICS_TOKEN', 'test-only-metrics')
    assert client.get('/metrics').status_code == 404
    assert client.get('/metrics', headers={'Authorization': 'Bearer wrong'}).status_code == 404
    client.get('/api/incidents/DO-NOT-LABEL-THIS-ID')
    response = client.get('/metrics', headers={'Authorization': 'Bearer test-only-metrics'})
    assert response.status_code == 200
    assert 'http_requests_total' in response.text
    assert 'websocket_connections_active' in response.text
    assert 'DO-NOT-LABEL-THIS-ID' not in response.text
    monkeypatch.delenv('METRICS_TOKEN')
    assert client.get('/metrics').status_code == 404


def test_structured_logging_drops_exception_values_and_sensitive_extras():
    token = request_id_var.set('correlation-test')
    identity = identity_var.set({'organization_id': 'org-test', 'user_id': 'user-test'})
    try:
        record = logging.LogRecord('app.test', logging.ERROR, '', 0, 'Operation failed', (),
                                   (ValueError, ValueError('raw-secret-value'), None))
        record.authorization = 'Bearer secret'
        record.password = 'secret-password'
        record.job_id = 'job-test'
        result = JsonFormatter().format(record)
        data = json.loads(result)
        assert data['request_id'] == 'correlation-test'
        assert data['organization_id'] == 'org-test'
        assert data['job_id'] == 'job-test'
        assert data['error_type'] == 'ValueError'
        assert 'secret' not in result and 'authorization' not in result
    finally:
        request_id_var.reset(token)
        identity_var.reset(identity)


def test_unexpected_error_preserves_request_id(app):
    from fastapi.testclient import TestClient
    @app.get('/test-failure')
    def failure():
        raise RuntimeError('sensitive error')
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/test-failure', headers={'X-Request-ID': 'error-correlation'})
        assert response.status_code == 500
        assert response.headers['X-Request-ID'] == 'error-correlation'
        assert 'sensitive' not in response.text
