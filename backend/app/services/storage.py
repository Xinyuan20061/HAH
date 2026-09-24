from pathlib import Path
from app.core.config import settings


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self):
        if settings.is_production:
            raise StorageError("production 禁止使用容器本地存储")
        self.root = Path(settings.upload_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def local_path(self, key: str):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise StorageError("invalid storage key")
        return path

    def public_url(self, key: str):
        return f"/uploads/{key}"

    def delete(self, key: str):
        path = self.root / key
        try:
            path.unlink(missing_ok=True)
        except TypeError:
            if path.exists():
                path.unlink()

    def delete_prefix(self, prefix: str):
        import shutil

        path = self.root / prefix.rstrip("/")
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


class CloudReferenceStorage:
    """CloudBase media is owned by wx.cloud, not by the Cloud Run container.

    The backend stores only cloud:// file IDs and short-lived download URLs. Mutating the
    actual object must be done by the mini program (or a separately authorized CloudBase SDK),
    which prevents accidental writes to the container's ephemeral filesystem.
    """

    def _unsupported(self, *_args, **_kwargs):
        raise StorageError("cloud_ref media must be managed through wx.cloud APIs")

    put_bytes = _unsupported
    local_path = _unsupported
    public_url = _unsupported
    delete = _unsupported
    delete_prefix = _unsupported


class S3Storage:
    def __init__(self):
        import boto3

        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name=settings.s3_region,
        )
        self.bucket = settings.s3_bucket
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception:
                pass

    def put_bytes(self, key, data, content_type=None):
        args = {"Bucket": self.bucket, "Key": key, "Body": data}
        if content_type:
            args["ContentType"] = content_type
        self.client.put_object(**args)
        return key

    def local_path(self, key):
        tmp = Path("/tmp/healthmate-media") / key
        tmp.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(tmp))
        return tmp

    def public_url(self, key):
        if settings.s3_public_base_url:
            return settings.s3_public_base_url.rstrip("/") + "/" + key
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600
        )

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix):
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kw)
            items = page.get("Contents") or []
            if items:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": x["Key"]} for x in items]},
                )
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")


def get_storage():
    backend = settings.storage_backend.lower()
    if backend == "s3":
        return S3Storage()
    if backend == "cloud_ref":
        return CloudReferenceStorage()
    if backend == "local" and not settings.is_production:
        return LocalStorage()
    raise StorageError("STORAGE_BACKEND 配置无效，禁止回退到 local")
