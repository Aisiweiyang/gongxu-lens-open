"""试点应用启动入口：python pilot/start_pilot.py [--host 127.0.0.1] [--port 8765]

首次使用流程：
1. python pilot/init_admin.py --username admin --password <8位以上>   （初始化管理员）
2. python pilot/start_pilot.py                                        （启动服务）
3. 浏览器打开 http://127.0.0.1:8765/ 登录工作台
4. 可选：python pilot/import_sample.py --task demo01                  （导入示例数据）
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot.db import Database
from pilot.server import make_server

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "pilot.sqlite3"


def main():
    parser = argparse.ArgumentParser(description="供需透镜试点应用（单组织独立部署）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    server = make_server(db, host=args.host, port=args.port)
    print(f"试点工作台已启动：http://{args.host}:{args.port}/（Ctrl+C 停止）")
    print(f"数据库：{db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
