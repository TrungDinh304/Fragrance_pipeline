"""DAG Airflow: canh những mặc định có thể phá cơ chế lịch sự với site.

KHÔNG CẦN CÀI AIRFLOW
Bộ này đọc file DAG bằng `ast`, không import chúng. Có chủ đích: Airflow chỉ nằm
trong ảnh của nó, nên một bài test đòi `import airflow` sẽ BỎ QUA trên mọi lần
`make test` — tức là không canh gì cả, đúng lúc cần canh nhất.

Thứ được canh ở đây là ba con số, và cả ba đều là mặc định của Airflow:

    catchup           mặc định True   -> xếp hàng mọi lượt đã lỡ, chạy một loạt
    max_active_runs   mặc định 16     -> nhiều lượt crawl song song
    retries           thường đặt > 0  -> bị chặn rồi vẫn gõ tiếp

Không cái nào gây lỗi. Cả ba chỉ làm tốc độ gõ cửa Fragrantica tăng lên, và hậu
quả hiện ra dưới dạng bị chặn IP — lúc đó đã muộn.

Phép kiểm "DAG có import được không" thì cần Airflow thật; nó nằm ở
`make airflow-check`, chạy trong ảnh Airflow.
"""

from __future__ import annotations

import ast
import pathlib

from runner import run  # noqa: E402

DAG_DIR = pathlib.Path(__file__).resolve().parent.parent / "airflow" / "dags"

# DAG nào CHẠM MẠNG. Những DAG này chịu luật ngặt hơn hẳn phần offline.
DAG_CHAM_MANG = {"fragrantica_crawl"}

# Khoi service `airflow` trong docker-compose.yml.
PATTERN_SERVICE_AIRFLOW = r"^  airflow:\n(.*?)(?=^  [a-z])"


def _file_dag() -> list[pathlib.Path]:
    return sorted(p for p in DAG_DIR.glob("*.py")
                  if not p.name.startswith("_"))


_KHONG_DOC_DUOC = object()


def _hang_so(node: ast.AST):
    """Giá trị của một literal, hoặc `_KHONG_DOC_DUOC` nếu nó là biểu thức.

    Dict được bóc TỪNG KHOÁ, không `literal_eval` cả cụm. Lý do cụ thể:
    `default_args` có lẫn `pendulum.duration(minutes=2)` — một lời gọi hàm — nên
    `literal_eval` ném lỗi và cả dict thành "không đọc được", kéo theo việc không
    kiểm được `retries` nằm ngay cạnh nó.
    """
    if isinstance(node, ast.Dict):
        ra = {}
        for k, v in zip(node.keys, node.values):
            if k is None:                     # cú pháp `**x`
                continue
            khoa = _hang_so(k)
            if khoa is _KHONG_DOC_DUOC:
                continue
            ra[khoa] = _hang_so(v)
        return ra
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _KHONG_DOC_DUOC


def _dag_decorators(path: pathlib.Path) -> list[dict]:
    """Mọi `@dag(...)` trong một file, trả về dict tham số đọc được."""
    cay = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ra = []
    for node in ast.walk(cay):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            ten = deco.func.id if isinstance(deco.func, ast.Name) else getattr(
                deco.func, "attr", "")
            if ten != "dag":
                continue
            ra.append({kw.arg: _hang_so(kw.value) for kw in deco.keywords
                       if kw.arg})
    return ra


def _moi_dag() -> list[tuple[pathlib.Path, dict]]:
    return [(p, d) for p in _file_dag() for d in _dag_decorators(p)]


# ------------------------------------------------------------ có DAG không
def test_co_it_nhat_mot_dag():
    """Chống việc cả bộ test xanh vì thư mục DAG rỗng."""
    assert _file_dag(), f"không có file DAG nào trong {DAG_DIR}"
    assert _moi_dag(), "có file DAG nhưng không khai @dag nào"


def test_moi_file_dag_phan_tich_duoc():
    """Lỗi cú pháp trong file DAG không làm hỏng gì khác — nó chỉ làm DAG biến
    mất khỏi UI, và lịch lặng lẽ ngừng chạy."""
    for p in _file_dag() + [DAG_DIR / "_chung.py"]:
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as exc:
            raise AssertionError(f"{p.name}: {exc}") from None


# ------------------------------------------------- ba mặc định nguy hiểm
def test_moi_dag_tat_catchup():
    """`catchup=True` (mặc định của Airflow) xếp hàng MỌI khoảng đã lỡ kể từ
    `start_date` rồi chạy chúng. Với DAG crawl thì đó là hàng chục lượt cùng lúc
    vào một site có rate limit thật."""
    for p, d in _moi_dag():
        assert d.get("catchup") is False, (
            f"{p.name}:{d.get('dag_id')} — catchup={d.get('catchup')!r}; "
            f"phải khai rõ catchup=False")


def test_moi_dag_chi_cho_mot_luot_chay():
    """Hai lượt song song cùng đọc một sổ ngân sách SQLite, mỗi bên tưởng mình
    còn đủ 150 request."""
    for p, d in _moi_dag():
        assert d.get("max_active_runs") == 1, (
            f"{p.name}:{d.get('dag_id')} — max_active_runs="
            f"{d.get('max_active_runs')!r}; phải là 1")


def test_dag_cham_mang_khong_duoc_retry():
    """ĐIỀU KHOẢN QUAN TRỌNG NHẤT CỦA BỘ NÀY.

    `daily` thoát với mã 2 khi bị chặn (429/Cloudflare). Retry nghĩa là bị chặn
    rồi vẫn gõ tiếp — phá đúng cái sổ `site_cooldown` sinh ra để tránh.

    Lượt hỏng thì đợi mai. Không mất dữ liệu: tiến độ được ghi ở mức từng chai.
    """
    thay = False
    for p, d in _moi_dag():
        if d.get("dag_id") not in DAG_CHAM_MANG:
            continue
        thay = True
        da = d.get("default_args") or {}
        assert isinstance(da, dict), f"{p.name}: không đọc được default_args"
        assert da.get("retries") == 0, (
            f"{p.name}:{d.get('dag_id')} — retries={da.get('retries')!r}; "
            f"DAG chạm mạng phải là 0")
    assert thay, (
        f"không thấy DAG nào trong {DAG_CHAM_MANG} — đổi tên DAG crawl mà quên "
        f"cập nhật danh sách này thì luật trên không còn canh gì")


def test_dag_offline_duoc_phep_retry():
    """Mặt còn lại: chuỗi offline tất định, chạy lại là an toàn và nên retry.

    Không có bài này thì "cứ đặt retries=0 cho hết" sẽ qua được mọi test, và khi
    đó một lỗi mạng vặt lúc nạp pgvector sẽ làm hỏng cả lượt.
    """
    for p, d in _moi_dag():
        if d.get("dag_id") in DAG_CHAM_MANG:
            continue
        da = d.get("default_args") or {}
        assert da.get("retries", 0) > 0, (
            f"{p.name}:{d.get('dag_id')} — DAG offline nên có retries > 0")


# ------------------------------------------------------------- vệ sinh DAG
def test_dag_id_khong_trung():
    ids = [d.get("dag_id") for _p, d in _moi_dag()]
    trung = {i for i in ids if ids.count(i) > 1}
    assert not trung, f"dag_id trùng: {trung}"


def test_moi_dag_co_dag_id_va_mo_ta():
    for p, d in _moi_dag():
        assert d.get("dag_id"), f"{p.name}: thiếu dag_id"
        assert d.get("description"), f"{p.name}: thiếu description"


def test_task_id_khong_trung_trong_mot_file():
    """Gán đè một biến task là tạo hai operator cùng `task_id`, và Airflow từ chối
    nạp cả DAG. Đã dính thật khi viết `refresh_marts.py`."""
    for p in _file_dag():
        cay = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        ids: list[str] = []
        for node in ast.walk(cay):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "task_id":
                    gt = _hang_so(kw.value)
                    if isinstance(gt, str):
                        ids.append(gt)
            # `lenh("ten", ...)` — task_id là tham số vị trí đầu tiên.
            ten = node.func.id if isinstance(node.func, ast.Name) else ""
            if ten == "lenh" and node.args:
                gt = _hang_so(node.args[0])
                if isinstance(gt, str):
                    ids.append(gt)
        trung = {i for i in ids if ids.count(i) > 1}
        assert not trung, f"{p.name}: task_id trùng {trung}"


def test_dag_khong_tu_bat_khi_moi_them():
    """DAG mới phải ở trạng thái tạm dừng cho tới khi có người bật.

    Khai trong compose (`AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION`), nên bài này
    canh file compose chứ không canh DAG. Không có nó thì vừa `up -d` xong là DAG
    crawl tự chạy lượt đầu, trước khi ai kịp đọc nó làm gì.
    """
    compose = (DAG_DIR.parent.parent / "docker-compose.yml").read_text(
        encoding="utf-8")
    assert 'AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: "true"' in compose, \
        "compose không đặt DAGS_ARE_PAUSED_AT_CREATION=true"


def test_khong_chay_hai_bo_lich_cung_luc():
    """Bộ lên lịch cũ (bash) phải nằm trong profile, không chạy mặc định.

    Chạy cả hai là tự nhân đôi tốc độ gõ cửa site: chúng dùng chung sổ ngân sách
    SQLite và mỗi bên đều tưởng mình còn đủ 150 request.
    """
    import re
    compose = (DAG_DIR.parent.parent / "docker-compose.yml").read_text(
        encoding="utf-8")
    khoi = re.search(r"^  scheduler:\n(.*?)(?=^  [a-z])", compose,
                     re.S | re.M)
    assert khoi, "không tìm thấy service `scheduler` trong compose"
    assert "profiles:" in khoi.group(1), (
        "service `scheduler` (bash, bản cũ) vẫn chạy mặc định — phải đưa vào "
        "profile để nó không chạy cùng Airflow")



# ------------------------------------------- biến môi trường không được trôi
def _goc() -> pathlib.Path:
    return DAG_DIR.parent.parent


def _compose() -> str:
    return (_goc() / "docker-compose.yml").read_text(encoding="utf-8")


def test_bien_start_sh_doc_deu_duoc_compose_truyen():
    """Mọi biến `start.sh` đọc phải được compose truyền vào container.

    LỖI ĐÃ XẢY RA THẬT, VÀ KHÔNG GÌ BẮT ĐƯỢC NÓ
    `start.sh` đọc `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` /
    `AIRFLOW_ADMIN_EMAIL`. Nhưng compose khai `AIRFLOW_USER`/`AIRFLOW_PASSWORD`
    rồi map sang, còn `.env.example` ghi tên thứ ba. Hậu quả:

      - đặt `AIRFLOW_PASSWORD` trong .env không có tác dụng gì;
      - `AIRFLOW_ADMIN_EMAIL` không bao giờ tới được container.

    Không có lỗi nào được in ra: `${VAR:-mặc_định}` của bash lặng lẽ dùng mặc
    định. Tên trôi đi khi gộp 4 container thành 1 và không ai đối chiếu lại.
    """
    import re
    sh = (_goc() / "scripts" / "airflow" / "start.sh").read_text(encoding="utf-8")
    # Biến đọc kiểu ${X:-mặc định} — tức là start.sh CHỜ compose truyền vào.
    doc = set(re.findall(r"\$\{(AIRFLOW_[A-Z_]+):-", sh))
    assert doc, "không tìm thấy biến nào trong start.sh — regex hỏng?"
    # Biến do CHÍNH ảnh Airflow đặt, không phải thứ compose cần truyền. Để trong
    # phép kiểm thì test đỏ oan và người ta sẽ thêm một dòng compose vô nghĩa.
    anh_tu_dat = {"AIRFLOW_HOME", "AIRFLOW_UID", "AIRFLOW_VERSION"}
    com = _compose()
    thieu = [v for v in sorted(doc - anh_tu_dat) if f"{v}:" not in com]
    assert not thieu, (
        f"start.sh đọc {thieu} nhưng docker-compose.yml không truyền. "
        f"Chúng sẽ im lặng dùng giá trị mặc định trong start.sh.")


def test_moi_bien_compose_thay_the_deu_co_trong_env_example():
    """Biến `${X}` trong compose phải được ghi trong .env.example.

    Thiếu thì người cài lần đầu không biết có thứ đó để đặt — và với mật khẩu thì
    họ chạy bằng mặc định mà không hay.
    """
    import re
    com = _compose()
    vd = (_goc() / ".env.example").read_text(encoding="utf-8")
    # Biến của Docker Compose, không phải của shell trong `command:`.
    dung = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::[-?]|\})", com))
    # Những biến do Docker/compose tự cấp hoặc chỉ dùng trong script nội bộ.
    bo_qua = {"HOSTNAME", "AIRFLOW_UID", "PGPASSWORD", "COMPOSE_NETWORK",
              "PERFUME_IMAGE", "COMPOSE_LLM_BASE_URL", "COMPOSE_S3_ENDPOINT",
              "S3_ENDPOINT", "AIRFLOW_FERNET_KEY", "EMBED_MODEL",
              "MINIO_API_PORT", "MINIO_CONSOLE_PORT", "API_PORT"}
    thieu = sorted(v for v in dung - bo_qua
                   if not re.search(rf"^#?\s*{v}=", vd, re.M))
    assert not thieu, f".env.example chưa ghi: {thieu}"


def test_khong_con_ten_bien_cu_da_bo():
    """Ten cu da bo khong duoc con sot trong CAU HINH.

    Bo qua dong chu thich co chu dich. Ban dau cua bai nay soi ca file va do ngay
    vi chinh doan chu thich giai thich "truoc day khai AIRFLOW_USER..." — ma ghi
    lai lich su mot lan doi ten dung la viec chu thich nen lam. Test phai soi
    cau hinh, khong soi van ban.
    """
    import re
    for ten in ("docker-compose.yml", ".env.example"):
        chu = (_goc() / ten).read_text(encoding="utf-8")
        for so, dong in enumerate(chu.splitlines(), 1):
            if dong.lstrip().startswith("#"):
                continue
            for cu in ("AIRFLOW_USER", "AIRFLOW_PASSWORD"):
                # Khop tron ten: AIRFLOW_ADMIN_USER khong phai AIRFLOW_USER.
                assert not re.search(rf"(?<![A-Z_]){cu}(?![A-Z_])", dong), (
                    f"{ten}:{so} con ten cu {cu} — da doi sang AIRFLOW_ADMIN_*: "
                    f"{dong.strip()}")


def test_ui_airflow_chi_mo_tren_localhost():
    """Socket Docker được mount vào container Airflow, nên ai đăng nhập được UI
    là có quyền ngang root trên máy host.

    Mặc định của Docker là bind mọi giao diện (0.0.0.0) — tức máy khác trong LAN
    cũng tới được, với mật khẩu mặc định `airflow/airflow`.
    """
    import re
    com = _compose()
    khoi = re.search(r"^  airflow:\n(.*?)(?=^  [a-z])", com, re.S | re.M)
    assert khoi, "không tìm thấy service `airflow`"
    cong = re.search(r'- "([^"]*):8080"', khoi.group(1))
    assert cong, "service `airflow` không publish cổng nào?"
    assert cong.group(1).startswith("127.0.0.1:"), (
        f"cổng UI bind {cong.group(1)!r} — phải là 127.0.0.1 vì container này "
        f"giữ socket Docker")



def test_standalone_phai_khai_execution_api_url():
    """Gop 4 container thanh 1 thi EXECUTION_API_SERVER_URL phai doi sang localhost.

    LOI DA XAY RA, VA HEALTHCHECK KHONG BAT DUOC
    Ban 4 container khai http://airflow-apiserver:8080/execution/. Khi gop lai
    mot container, ten host do khong con phan giai duoc. Thieu dong nay thi task
    do voi `httpcore.ConnectError: [Errno 111] Connection refused` — trong khi
    container van bao "healthy", vi healthcheck chi hoi api-server con song hay
    khong, khong hoi task co chay duoc hay khong.

    Nghia la: dieu phoi "xanh" ma khong chay duoc gi. Day la bai canh dung cho do.
    """
    import re
    com = _compose()
    khoi = re.search(PATTERN_SERVICE_AIRFLOW, com, re.S | re.M)
    assert khoi, "khong tim thay service `airflow`"
    than = khoi.group(1)
    assert "AIRFLOW__CORE__EXECUTION_API_SERVER_URL" in than, (
        "service `airflow` khong khai EXECUTION_API_SERVER_URL — task se do voi "
        "Connection refused trong khi container van healthy")
    url = re.search(r'EXECUTION_API_SERVER_URL:\s*"?([^"\n]+)', than)
    assert url and "localhost" in url.group(1), (
        f"EXECUTION_API_SERVER_URL = {url and url.group(1)!r}; mot container thi "
        f"phai tro ve localhost")


def test_khong_khai_auth_manager_vi_standalone_ghi_de():
    """`airflow standalone` ghi de cung AUTH_MANAGER thanh SimpleAuthManager.

    Khai FabAuthManager o compose la mot dong noi sai su that — va no da lam mat
    nua gio: `airflow users list` hien admin day du, config get-value tra ve FAB,
    ma UI van tra "Invalid credentials", vi api-server dung SimpleAuthManager va
    khong tra bang FAB.
    """
    import re
    com = _compose()
    for so, dong in enumerate(com.splitlines(), 1):
        if dong.lstrip().startswith("#"):
            continue
        assert "AIRFLOW__CORE__AUTH_MANAGER" not in dong, (
            f"docker-compose.yml:{so} khai AUTH_MANAGER, nhung standalone ghi de "
            f"no — dong nay khong co tac dung: {dong.strip()}")


def test_dang_nhap_tat_dinh_qua_simple_auth_manager():
    """Thong tin dang nhap phai den tu .env, khong phai mat khau sinh ngau nhien.

    `airflow standalone` sinh mat khau ngau nhien vao
    standalone_admin_password.txt neu khong khai `simple_auth_manager_users` —
    ma start.sh lai XOA file do khi don PID. Ket qua: khong ai biet mat khau.
    """
    com = _compose()
    assert "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS" in com,         "compose khong khai simple_auth_manager_users"
    assert "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE" in com,         "compose khong khai simple_auth_manager_passwords_file"
    sh = (_goc() / "scripts" / "airflow" / "start.sh").read_text(encoding="utf-8")
    assert "simple_auth_passwords.json" in sh,         "start.sh khong ghi file mat khau cho SimpleAuthManager"
    # Phan FAB cu phai bi bo han: de lai la code chet, va doc vao se tuong no
    # dang quyet dinh viec dang nhap.
    for cu in ("airflow users create", "airflow users reset-password"):
        for dong in sh.splitlines():
            if dong.lstrip().startswith("#"):
                continue
            assert cu not in dong, (
                f"start.sh con `{cu}` — standalone dung SimpleAuthManager nen "
                f"lenh nay khong anh huong gi toi dang nhap")


if __name__ == "__main__":
    raise SystemExit(run(globals()))
