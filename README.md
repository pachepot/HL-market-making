# 🤖 Hyperliquid Market Maker Bot

Hyperliquid 거래소용 마켓메이커 봇

## 📋 사전 준비

1. `config.json`에 다음 정보를 입력하세요:
   - `address`: 지갑 주소
   - `pkey`: Private Key

## 🚀 실행 방법

### Spot 마켓메이킹 (UBTC)
```bash
python hyperliquid_spot_mm.py
```

### HIP3 마켓메이킹 (XYZ)
```bash
python hyperliquid_hip_mm.py
```

## ⚙️ 설정

`config.json` 예시:
```json
{
  "address": "YOUR_WALLET_ADDRESS",
  "pkey": "YOUR_PRIVATE_KEY"
}
```

> ⚠️ **주의**: `config.json`은 민감한 정보를 포함하고 있으므로 절대 공유하거나 커밋하지 마세요!

## 📝 파일 구조

- `hyperliquid_spot_mm.py` - Spot 거래 전용 (주로 UBTC)
- `hyperliquid_hip_mm.py` - HIP3 토큰 전용 (주로 XYZ)
- `config.json` - 지갑 설정 파일 (Git에서 제외됨)

## 🛡️ 보안

Private Key는 안전하게 보관하고, 절대 타인과 공유하지 마세요.
