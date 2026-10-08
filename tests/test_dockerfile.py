from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_dockerfile_sets_default_bind_port_for_runtime_and_healthcheck():
    dockerfile = DOCKERFILE.read_text()

    assert "ENV HOST=0.0.0.0" in dockerfile
    assert "ENV PORT=5690" in dockerfile
    assert "http://localhost:${PORT:-5690}/health" in dockerfile


def test_runtime_image_does_not_carry_node_modules():
    """node_modules is build-only: copy-assets.js copies the vendor files the
    templates load, so the builder drops it before the runtime COPY of app/."""
    dockerfile = DOCKERFILE.read_text()
    builder = dockerfile.split("FROM deps AS builder", 1)[1].split("# ─── Stage 3")[0]
    assert "rm -rf app/static/node_modules" in builder
    assert builder.index("run build") < builder.index("rm -rf app/static/node_modules")
