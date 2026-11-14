"""运行领域摘要或启动本地供数服务。"""

import argparse
import json
from pathlib import Path
from http.server import ThreadingHTTPServer

from .analysis import dynamic_summary
from .datasource import SourceClient
from .reports import package_report, save_report
from .revisions import compare_revisions
from .service import handler_for, load_repository, serve


def main():
    parser = argparse.ArgumentParser(description="领域资料摘要")
    parser.add_argument("--dataset", default="fixtures/dataset.json")
    parser.add_argument("--source-url")
    parser.add_argument("--output", default="/tmp/domain-summary.json")
    parser.add_argument("--baseline", help="上一代完整资料文件，提供后输出新增需求与旧账更正对账")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    schema_path = Path(__file__).with_name("schema.json")
    if arguments.serve:
        server = ThreadingHTTPServer(("127.0.0.1", arguments.port), handler_for(load_repository(arguments.dataset, schema_path)))
        try:
            print(json.dumps({"listening": server.server_address}, ensure_ascii=False), flush=True)
            server.serve_forever()
        finally:
            server.server_close()
        return
    if arguments.source_url:
        snapshot = SourceClient(arguments.source_url).read()
    else:
        with serve(load_repository(arguments.dataset, schema_path)) as base_url:
            snapshot = SourceClient(base_url).read()
    summary = dynamic_summary(snapshot.tables)
    if arguments.baseline:
        previous_dataset = json.loads(Path(arguments.baseline).read_text(encoding="utf-8"))
        previous_generation = previous_dataset["generation"]
        if previous_generation == snapshot.generation:
            raise SystemExit("修订对账要求两代资料的 generation 不同；同代内容改写已被供数端拒绝")
        summary["revisions"] = compare_revisions(
            previous_dataset["tables"], snapshot.tables,
            previous_generation=previous_generation,
            current_generation=snapshot.generation)
    report = package_report(snapshot, summary)
    save_report(arguments.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
