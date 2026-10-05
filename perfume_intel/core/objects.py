"""Cổng kho đối tượng. Đây là ranh giới, không phải nơi xử lý.

VÌ SAO KHÔNG GIẢ VỜ S3 LÀ MỘT Ổ ĐĨA
Có cách làm tốn ít dòng hơn hẳn: dùng `fsspec`/`UPath` để `s3://...` trông y như
một `pathlib.Path`, rồi không sửa chỗ nào khác. Không chọn cách đó, vì cái khác
nhau quan trọng nhất sẽ bị che đi đúng chỗ nó gây hại:

    S3 KHÔNG CÓ APPEND. Một object chỉ ghi được NGUYÊN CON.

Mà `crawl_urls` ghi từng dòng NGAY KHI có — đó chính là lý do một mẻ 10 ngày bị
đứt mạng không mất dữ liệu. Nếu `Path` kiểu S3 lặng lẽ không nhận mode "a", chỗ
vỡ sẽ xuất hiện ở giờ thứ 200 của mẻ crawl, không phải lúc viết code. Nên cổng
này cố ý chỉ có `put` nguyên object: ràng buộc hiện ra ngay ở chữ ký hàm, và
`core/lake.py` phải xử lý nó tử tế (spool local rồi niêm).

HAI LUẬT GIỮ CHO RANH GIỚI NÀY KHÔNG RÒ
  1. **Khoá là chuỗi, phân cách bằng `/`, không mở đầu bằng `/`.** Không phải
     `Path`: trên Windows `Path` sẽ biến khoá thành `a\\b` và cùng một object có
     hai cái tên.
  2. **`prefix` so khớp theo CHUỖI, không theo thư mục** — đúng như S3. Nên
     `list("fragrantica/per")` khớp cả `fragrantica/perfumes/...`. Bản local phải
     bắt chước đúng điều này; nếu không hai adapter cùng xanh mà hành vi khác
     nhau, và chỗ lệch chỉ lộ ra sau khi đã chuyển sang MinIO.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# S3 cho phép tới 1024 byte. Chặn ở đây để lỗi hiện ra lúc GHI, không phải lúc
# đọc lại rồi thấy thiếu object.
MAX_KEY = 1024

# Hậu tố file ghi dở của LocalStore. Không bao giờ được hiện ra trong `list()`.
TMP_MARK = ".__tmp"


class ObjectNotFound(KeyError):
    """Khoá không có trong kho."""


class StoreUnavailable(RuntimeError):
    """Không nói chuyện được với kho (chưa cài client, sai endpoint, sai khoá).

    Tách riêng khỏi `ObjectNotFound` vì hai thứ đòi hai cách xử lý khác nhau:
    thiếu một object là chuyện bình thường, còn mất cả kho là lúc phải DỪNG, chứ
    không được âm thầm coi như kho rỗng rồi crawl lại từ đầu.
    """


@dataclass(frozen=True)
class ObjectInfo:
    """Những gì biết được về một object mà không phải tải nó về."""

    key: str
    size: int
    modified: float          # epoch giây


def check_key(key: str) -> str:
    """Chuẩn hoá và từ chối khoá sai dạng.

    Từ chối sớm, vì khoá sai dạng không gây lỗi: nó tạo ra một object thứ hai nằm
    cạnh object thật (vd `a//b` bên cạnh `a/b`), và phía đọc chỉ thấy thiếu dữ
    liệu mà không thấy lý do.
    """
    if not isinstance(key, str):
        raise ValueError(f"Khoá phải là chuỗi, nhận {type(key).__name__}.")
    key = key.replace("\\", "/")
    if not key or key != key.strip():
        raise ValueError(f"Khoá rỗng hoặc có khoảng trắng ở hai đầu: {key!r}")
    if key.startswith("/") or key.endswith("/"):
        raise ValueError(f"Khoá không được mở đầu hay kết thúc bằng '/': {key!r}")
    if "//" in key:
        raise ValueError(f"Khoá có đoạn rỗng: {key!r}")
    if any(part in (".", "..") for part in key.split("/")):
        raise ValueError(f"Khoá không được chứa '.' hay '..': {key!r}")
    if len(key.encode("utf-8")) > MAX_KEY:
        raise ValueError(f"Khoá dài quá {MAX_KEY} byte: {key[:60]}...")
    return key


@runtime_checkable
class ObjectStore(Protocol):
    """Mọi adapter phải nói đúng ngần này.

    Bản nào cũng phải qua `tests/objectstore_contract.py` — bộ test đó là định
    nghĩa thật của hợp đồng này, phần chữ chỉ là giải thích.
    """

    def put(self, key: str, data: bytes) -> ObjectInfo:
        """Ghi NGUYÊN object. Khoá đã có thì ghi đè."""
        ...

    def get(self, key: str) -> bytes:
        """Đọc nguyên object. Không có thì ném ObjectNotFound."""
        ...

    def stat(self, key: str) -> ObjectInfo | None:
        """Thông tin object, hoặc None nếu không có. Không tải nội dung."""
        ...

    def list(self, prefix: str = "") -> list[ObjectInfo]:
        """Mọi object có khoá bắt đầu bằng `prefix`, đã sắp theo khoá."""
        ...

    def delete(self, key: str) -> bool:
        """True nếu có object và đã xoá; False nếu vốn không có."""
        ...


# --------------------------------------------------------------------- local
class LocalStore:
    """Kho đối tượng trên ổ đĩa. Mặc định, và là thứ mọi test chạy trên.

    Không phải thứ bỏ đi: một máy lẻ không cần MinIO, và test phải chạy được khi
    không có mạng. Nó còn là bên ĐỐI CHỨNG — cùng một bộ hợp đồng chạy cho cả
    hai adapter, nên hành vi lệch nhau sẽ đỏ ngay, chứ không chờ tới lúc đã
    chuyển kho mới phát hiện.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def __str__(self) -> str:
        return f"file://{self.root.as_posix()}"

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    @staticmethod
    def _info(key: str, path: Path) -> ObjectInfo:
        st = path.stat()
        return ObjectInfo(key=key, size=st.st_size, modified=st.st_mtime)

    def put(self, key: str, data: bytes) -> ObjectInfo:
        key = check_key(key)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Ghi tạm rồi đổi tên: chết giữa lúc ghi thì object cũ còn NGUYÊN, chứ
        # không để lại một file nửa vời mà phía đọc tưởng là đủ.
        tmp = path.with_name(f"{path.name}{TMP_MARK}{os.getpid()}")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return self._info(key, path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
            raise ObjectNotFound(key) from exc

    def stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return self._info(check_key(key), path)

    def list(self, prefix: str = "") -> list[ObjectInfo]:
        if not self.root.is_dir():
            return []
        out = []
        for path in self.root.rglob("*"):
            if not path.is_file() or TMP_MARK in path.name:
                continue
            key = path.relative_to(self.root).as_posix()
            # So khớp theo CHUỖI, không theo thư mục — bắt chước S3. Xem docstring
            # của module.
            if key.startswith(prefix):
                out.append(self._info(key, path))
        return sorted(out, key=lambda o: o.key)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True


# ------------------------------------------------------------------------ s3
class S3Store:
    """Kho đối tượng qua API S3: MinIO hôm nay, AWS/R2/Wasabi sau này.

    Dùng `boto3` chứ không phải SDK riêng của MinIO, vì S3 API mới là thứ bền:
    cùng đoạn code này chạy với MinIO tự host và với AWS S3, chỉ đổi biến môi
    trường. Một dòng `import minio` sẽ neo cả project vào một nhà cung cấp ngay ở
    tầng thấp nhất — đúng thứ `docs/ARCHITECTURE.md` nói là phải tránh.
    """

    def __init__(self, bucket: str, endpoint: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None,
                 region: str = "us-east-1") -> None:
        self.bucket = bucket
        self.endpoint = endpoint
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = None

    def __str__(self) -> str:
        where = f" @ {self.endpoint}" if self.endpoint else ""
        return f"s3://{self.bucket}{where}"

    # ------------------------------------------------------------- kết nối
    @property
    def client(self):
        if self._client is None:
            self._client = self._connect()
        return self._client

    def _connect(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StoreUnavailable(
                "Cần boto3 để nói chuyện với MinIO/S3. Cài bằng:\n"
                '    pip install -e ".[lake]"') from exc
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            # path-style là BẮT BUỘC khi trỏ MinIO bằng host/IP: kiểu
            # virtual-host sẽ thành `http://bronze.minio:9000`, một tên không
            # phân giải được.
            config=Config(s3={"addressing_style": "path"},
                          retries={"max_attempts": 3, "mode": "standard"}),
        )

    def _wrap(self, exc: Exception, key: str = "") -> Exception:
        """Lỗi của botocore -> lỗi của cổng này."""
        from botocore.exceptions import BotoCoreError, ClientError
        if isinstance(exc, ClientError):
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NoSuchBucket", "NotFound"):
                return ObjectNotFound(key or code)
            return StoreUnavailable(f"{self}: {code} — {exc}")
        if isinstance(exc, BotoCoreError):
            return StoreUnavailable(f"{self}: {exc}")
        return exc

    def ensure_bucket(self) -> None:
        """Tạo bucket nếu chưa có. Gọi TƯỜNG MINH, không gọi trong `__init__`.

        Dựng kho là việc quản trị, không phải tác dụng phụ của việc mở kết nối:
        gõ sai tên bucket lúc đọc thì phải báo thiếu, không được im lặng tạo ra
        một bucket rỗng rồi kết luận là chẳng có dữ liệu nào.
        """
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except BotoCoreError as exc:
            raise self._wrap(exc) from exc
        except ClientError:
            pass
        try:
            self.client.create_bucket(Bucket=self.bucket)
            log.info("Đã tạo bucket %s", self.bucket)
        except (ClientError, BotoCoreError) as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise self._wrap(exc) from exc

    # --------------------------------------------------------------- thao tác
    def put(self, key: str, data: bytes) -> ObjectInfo:
        from botocore.exceptions import BotoCoreError, ClientError
        key = check_key(key)
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        except (ClientError, BotoCoreError) as exc:
            raise self._wrap(exc, key) from exc
        # Không dựa vào head ngay sau put: nếu kho chưa kịp thấy thì vẫn báo đúng
        # số byte mình vừa gửi.
        return self.stat(key) or ObjectInfo(key=key, size=len(data),
                                            modified=time.time())

    def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError
        key = check_key(key)
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
            return body.read()
        except (ClientError, BotoCoreError) as exc:
            raise self._wrap(exc, key) from exc

    def stat(self, key: str) -> ObjectInfo | None:
        from botocore.exceptions import BotoCoreError, ClientError
        key = check_key(key)
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            wrapped = self._wrap(exc, key)
            if isinstance(wrapped, ObjectNotFound):
                return None
            raise wrapped from exc
        return ObjectInfo(key=key, size=int(head["ContentLength"]),
                          modified=head["LastModified"].timestamp())

    def list(self, prefix: str = "") -> list[ObjectInfo]:
        from botocore.exceptions import BotoCoreError, ClientError
        out: list[ObjectInfo] = []
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix)
            for page in pages:
                for item in page.get("Contents", []):
                    out.append(ObjectInfo(
                        key=item["Key"], size=int(item["Size"]),
                        modified=item["LastModified"].timestamp()))
        except (ClientError, BotoCoreError) as exc:
            wrapped = self._wrap(exc, prefix)
            # Bucket chưa tồn tại thì coi như kho rỗng, giống thư mục chưa có ở
            # bản local. Nếu không, hai adapter lệch nhau ngay lần chạy đầu.
            if isinstance(wrapped, ObjectNotFound):
                return []
            raise wrapped from exc
        return sorted(out, key=lambda o: o.key)

    def delete(self, key: str) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError
        key = check_key(key)
        if self.stat(key) is None:
            return False
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise self._wrap(exc, key) from exc
        return True


# ------------------------------------------------------------------ tiện ích
def copy_tree(src: ObjectStore, dst: ObjectStore, prefix: str = "") -> int:
    """Sao mọi object từ kho này sang kho khác. Dùng khi di trú."""
    moved = 0
    for info in src.list(prefix):
        dst.put(info.key, src.get(info.key))
        moved += 1
    return moved


def total_size(infos: Iterable[ObjectInfo]) -> int:
    return sum(info.size for info in infos)


def disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free
