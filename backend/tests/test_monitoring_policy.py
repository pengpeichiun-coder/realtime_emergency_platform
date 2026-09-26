from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def read(name):
    return yaml.safe_load((ROOT / name).read_text())


def test_monitoring_is_opt_in_and_only_publishes_loopback_ports():
    normal = read("compose.yaml")["services"]
    optional = read("compose.monitoring.yaml")["services"]
    services = {"prometheus", "alertmanager", "alert-receiver", "monitoring-token"}
    assert services.isdisjoint(normal)
    for name in services:
        assert optional[name]["profiles"] == ["monitoring"]
        assert all(port.startswith("127.0.0.1:") for port in optional[name].get("ports", []))
    assert "monitoring" in optional["api2"]["profiles"]


def test_real_scrape_inventory_auth_file_and_internal_allowed_hosts():
    config = read("monitoring/prometheus.yml")
    scrape, = config["scrape_configs"]
    assert scrape["job_name"] == "emergency-api" and scrape["metrics_path"] == "/metrics"
    assert scrape["authorization"] == {"type": "Bearer", "credentials_file": "/run/monitoring/metrics_token"}
    assert scrape["static_configs"] == [{"targets": ["api:8000", "api2:8000"], "labels": {"cluster": "local"}}]
    services = read("compose.monitoring.yaml")["services"]
    for name in ("api", "api2"):
        assert services[name]["environment"]["ALLOWED_HOSTS"].endswith(",api,api2")
    assert "monitoring-credentials:/run/monitoring:ro" in services["prometheus"]["volumes"]
    assert services["prometheus"]["depends_on"]["monitoring-token"]["condition"] == "service_completed_successfully"


def test_only_local_receiver_gets_firing_and_resolved_notifications():
    config = read("monitoring/alertmanager.yml")
    assert config["route"]["receiver"] == "local-test-only"
    receiver, = config["receivers"]
    assert set(receiver) == {"name", "webhook_configs"}
    webhook, = receiver["webhook_configs"]
    assert webhook["url"] == "http://alert-receiver:8080/alerts"
    assert webhook["send_resolved"] is True and webhook["timeout"] == "5s"


def test_rehearsal_preserves_normal_thresholds_and_replica_logic():
    normal = next(rule for rule in read("monitoring/alerts.yml")["groups"][0]["rules"]
                  if rule["alert"] == "EmergencyOutboxPublisherUnavailable")
    rehearsal, = read("monitoring/alerts.rehearsal.yml")["groups"][0]["rules"]
    assert normal["for"] == "2m" and "> bool 30" in normal["expr"]
    assert rehearsal["expr"] == normal["expr"].replace("> bool 30", "> bool 10")
    assert rehearsal["for"] == "10s"
    assert "rehearsal" not in (ROOT / "compose.monitoring.yaml").read_text()
    assert read("monitoring/prometheus.yml")["rule_files"] == ["alerts.yml"]


def test_config_validation_remains_in_existing_required_infrastructure_check():
    check = str(read(".github/workflows/ci.yml")["jobs"]["infrastructure"])
    assert "make monitoring-validate" in check
    assert "265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8" in check
    assert "alertmanager-0.34.1.linux-amd64" in check
    assert "alert-rehearsal" not in check  # Long live rehearsal stays local/manual.
