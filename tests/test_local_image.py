# -*- coding: utf-8 -*-
import gzip

from ucm_deployer.core import local_image


def test_validate_image_ref_accepts_registry_reference():
    ref = "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler"
    assert local_image.validate_image_ref(ref) == ref


def test_validate_image_ref_rejects_protocol_and_shell_characters():
    for ref in ("https://quay.io/ascend/vllm-ascend:v0.30.0", "a b", "a;b"):
        try:
            local_image.validate_image_ref(ref)
        except local_image.LocalImageError:
            continue
        raise AssertionError(f"应拒绝非法镜像地址: {ref}")


def test_platform_from_arch():
    assert local_image.platform_from_arch("aarch64") == "linux/arm64"
    assert local_image.platform_from_arch("x86_64") == "linux/amd64"
    assert local_image.platform_from_arch("") == ""


def test_pull_image_to_tar_creates_gzip_archive(monkeypatch, tmp_path):
    calls = []

    def fake_pull(ref, platform, log, cancelled):
        calls.append((ref, platform))
        log("pull ok")

    def fake_save(ref, output_path, log):
        calls.append(("save", ref))
        output_path.write_bytes(b"docker-image-tar" * 1024)

    monkeypatch.setattr(local_image, "_docker_pull", fake_pull)
    monkeypatch.setattr(local_image, "_docker_save", fake_save)
    result = local_image.pull_image_to_tar(
        "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler",
        platform="linux/arm64", output_dir=str(tmp_path))

    assert result.endswith("image.tar.gz")
    with gzip.open(result, "rb") as archive:
        assert archive.read() == b"docker-image-tar" * 1024
    assert calls[0] == ("quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler",
                        "linux/arm64")
    assert calls[1] == ("save", "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler")
