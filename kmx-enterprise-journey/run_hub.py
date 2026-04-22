"""허브 서버 실행 (같은 폴더에서: python run_hub.py)."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "hub.main:app",
        host="0.0.0.0",
        port=8090,
        reload=True,
    )
