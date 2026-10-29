"""Guard the public rate-limit configuration against shared-tunnel regressions."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_ip_trust_is_exact_tunnel_peer_not_docker_subnet():
    config = (ROOT / 'nginx/nginx.conf').read_text(encoding='utf-8')
    assert 'set_real_ip_from 172.18.0.3;' in config
    assert 'set_real_ip_from 172.18.0.0/16;' not in config
    assert 'real_ip_header CF-Connecting-IP;' in config
    assert 'limit_req_zone $binary_remote_addr zone=general:' in config


def test_scan_paths_do_not_consume_the_general_site_limit():
    config = (ROOT / 'nginx/conf.d/djredoo.conf').read_text(encoding='utf-8')
    assert config.count('location ~*') >= 2
    assert '(env|git)' in config
    assert r'\.php' in config
    assert 'return 404;' in config
    assert config.count('limit_req zone=general') == 2
    assert 'limit_req zone=login burst=3' in config
    assert 'limit_req zone=api_write burst=5' in config
