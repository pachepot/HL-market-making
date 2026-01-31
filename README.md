# Hyperliquid Market Maker Bot

## ⚠️ 면책 조항 (DISCLAIMER)

**본 봇은 수익 창출을 목적으로 하지 않으며, 볼륨 작업(Volume Making) 용도로 제작되었습니다.**

- 본 소프트웨어를 사용하여 발생하는 모든 손실에 대해 개발자는 책임을 지지 않습니다
- 실제 자금으로 사용 시 손실이 발생할 수 있습니다
- 사용 전 충분한 테스트를 거치고, 소액으로 시작할 것을 강력히 권장합니다

---

## 설치

### 1. 필요한 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. `config.json`에 정보 입력
```json
{
  "address": "YOUR_WALLET_ADDRESS",
  "pkey": "YOUR_PRIVATE_KEY"
}
```

## 실행 방법

### Spot 마켓메이킹 (UBTC)
```bash
python hyperliquid_spot_mm.py
```

### HIP3 마켓메이킹 (BTC)
```bash
python hyperliquid_futures_mm.py
```

### HIP3 마켓메이킹 (XYZ)
```bash
python hyperliquid_hip_mm.py
```

## 설정 파라미터

### 기본 설정
| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `ORDER_SIZE_USD` | 300 | 주문할 USD 금액 |
| `CHECK_INTERVAL` | 60 | 주문을 취소하고 재배치할 간격 (초) |
| `MAX_OPEN_ORDERS` | 30 | 오픈 주문의 최대 수량 (한 사이드당) |
| `ORDER_EXPIRY_MINUTES` | 10 | 주문 만료 시간 (N분 후 자동 취소) |
| `MAX_POSITION_USD` | 30000 | 최대 포지션 크기 (달러 기준) |

### 스프레드 및 주문 분배
| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `BUY_SPREADS` | [0.1%, 0.2%, 0.3%, 0.4%, 0.5%] | 5단계 매수 스프레드 |
| `SELL_SPREADS` | [0.1%, 0.2%, 0.3%, 0.4%, 0.5%] | 5단계 매도 스프레드 |
| `ORDER_RATIOS` | [0.5, 0.2, 0.1, 0.1, 0.1] | 각 단계별 주문 금액 비율 |

### 인벤토리 관리
| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `MIN_SELL_RATIO` | 0.1 | 최소 매도 비율 (포트폴리오의 10% 이상 보유 시 매도) |
| `MAX_COIN_RATIO` | 0.7 | 최대 코인 보유 비율 (70% 초과 시 매수 중단) |
| `TARGET_COIN_RATIO` | 0.5 | 목표 코인 비율 (50% 균형 유지) |
| `INVENTORY_SKEW_MULTIPLIER` | 1.5 | 인벤토리 불균형 시 스프레드 조정 배수 |

### ATR 기반 변동성 조정
| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `ATR_INTERVAL` | '5m' | ATR 계산에 사용할 캔들 간격 |
| `ATR_PERIOD` | 14 | ATR 계산 기간 |
| `BASE_SPREAD` | 0.001 | 기준 스프레드 (0.1%) |
| `VOL_MULTIPLIER_MIN` | 0.5 | 최소 변동성 배수 (스프레드 50%까지 축소) |
| `VOL_MULTIPLIER_MAX` | 2.0 | 최대 변동성 배수 (스프레드 200%까지 확대) |

### 주문 분배 예시
`ORDER_SIZE_USD = 300`, `ORDER_RATIOS = [0.5, 0.2, 0.1, 0.1, 0.1]`인 경우:
- 1단계 (0.1% 스프레드): $150
- 2단계 (0.2% 스프레드): $60
- 3단계 (0.3% 스프레드): $30
- 4단계 (0.4% 스프레드): $30
- 5단계 (0.5% 스프레드): $30

## 팁 및 주의사항

- **선물이나 xyz의 경우 미리 하이퍼리퀴드에서 레버리지를 낮춰두는 것을 매우 추천합니다**
- **소액으로 먼저 테스트한 후 실전 운영하세요**
- 변동성이 높은 시장에서는 `ATR_PERIOD`를 줄여 빠르게 반응
- 인벤토리 관리를 강화하려면 `INVENTORY_SKEW_MULTIPLIER`를 높임
- 체결률을 높이려면 `BUY_SPREADS`/`SELL_SPREADS`를 줄임
