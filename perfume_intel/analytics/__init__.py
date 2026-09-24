"""Pipeline phân tích thị trường dựa trên tín hiệu cộng đồng.

Ba tầng tách rời nhau:
    dataset.py  nạp .jsonl đã crawl -> list[Row] phẳng
    metrics.py  Row -> các bảng chỉ số (hàm thuần, test được)
    report.py   chạy chỉ số rồi ghi ra data/processed/
"""
