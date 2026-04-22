# 시나리오 목록


| ID      | 파일                                    | 의도                                  |
| ------- | ------------------------------------- | ----------------------------------- |
| pass-01 | `pass/01_nominal.json`                | 샘플과 유사한 정상 한 건                      |
| pass-02 | `pass/02_minimal_required.json`       | 필수 필드만 (옵션 필드 생략)                   |
| pass-03 | `pass/03_fault_status.json`           | `FAULT` + 알람 (상태 분기)                |
| pass-04 | `pass/04_zero_cycle_idle.json`        | `cycle_time_ms` 0, 저전력 IDLE         |
| pass-05 | `pass/05_joint_positions_list.json`   | `joint_positions_deg` 배열 형태         |
| pass-06 | `pass/06_long_ids_sme.json`           | 긴 `line_id` / `station_id` (문자열 엣지) |
| fail-01 | `fail_validate/missing_robot_id.json` | `robot_id` 누락 → 검증 실패 기대            |
| fail-02 | `fail_validate/bad_cycle_type.json`   | `cycle_time_ms` 비숫자 → 검증 실패 기대      |


`verify_matrix.py` 가 `pass/` 와 `fail_validate/` 아래의 모든 `.json` 을 순회합니다.