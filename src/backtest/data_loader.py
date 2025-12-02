"""
回测数据加载器
支持从Hyperliquid API或本地文件加载历史K线数据
"""

import pandas as pd
import json
from typing import Optional
from datetime import datetime
from pathlib import Path

from hyperliquid.info import Info
from hyperliquid.utils import constants


class BacktestDataLoader:
    """回测数据加载器"""

    def __init__(self, testnet: bool = False):
        """
        初始化数据加载器
        
        Args:
            testnet: 是否使用测试网（仅用于API数据源）
        """
        self.testnet = testnet
        self.base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = Info(self.base_url, skip_ws=True) if not testnet else None

    def load_from_api(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime
    ) -> Optional[pd.DataFrame]:
        """
        从Hyperliquid API加载历史K线数据
        
        Args:
            symbol: 交易对符号
            timeframe: 时间周期（1m, 5m, 15m, 1h, 4h, 1d）
            start_date: 开始时间
            end_date: 结束时间
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            # 转换为毫秒时间戳
            start_time = int(start_date.timestamp() * 1000)
            end_time = int(end_date.timestamp() * 1000)
            
            print(f"📥 从API加载 {symbol} 历史数据 ({timeframe})")
            print(f"   时间范围: {start_date.strftime('%Y-%m-%d %H:%M:%S')} 至 {end_date.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 获取K线数据
            candles = self.info.candles_snapshot(
                name=symbol,
                interval=timeframe,
                startTime=start_time,
                endTime=end_time
            )
            
            if not candles:
                print(f"⚠️ 没有获取到 {symbol} 的历史数据")
                return None
            
            # 转换为 DataFrame
            df = pd.DataFrame(candles)
            
            # 重命名列
            column_mapping = {
                't': 'timestamp',
                'o': 'open',
                'h': 'high',
                'l': 'low',
                'c': 'close',
                'v': 'volume'
            }
            
            # 只选择存在的列
            df = df[[col for col in column_mapping.keys() if col in df.columns]]
            df = df.rename(columns=column_mapping)
            
            # 确保有必需的列
            required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in required_cols):
                print(f"⚠️ K线数据缺少必需的列: {df.columns.tolist()}")
                return None
            
            # 转换数据类型
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # 按时间排序
            df = df.sort_values('timestamp').reset_index(drop=True)
            
            # 只保留需要的列
            df = df[required_cols]
            
            # 过滤时间范围（确保在指定范围内）
            df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]
            
            print(f"✅ 加载完成: {len(df)} 条K线数据")
            
            return df
            
        except Exception as e:
            print(f"❌ 从API加载数据失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def load_from_file(
        self,
        file_path: str,
        symbol: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        从本地文件加载历史K线数据
        
        支持格式：
        - CSV: 必须包含 timestamp, open, high, low, close, volume 列
        - JSON: 数组格式，每个元素包含上述字段
        
        Args:
            file_path: 文件路径
            symbol: 交易对符号（可选，用于日志）
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            path = Path(file_path)
            if not path.exists():
                print(f"❌ 文件不存在: {file_path}")
                return None
            
            print(f"📥 从文件加载历史数据: {file_path}")
            
            # 根据文件扩展名选择加载方式
            if path.suffix.lower() == '.csv':
                df = pd.read_csv(file_path)
            elif path.suffix.lower() == '.json':
                # 兼容数组、字典包装以及 JSONL 格式
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    parsed = json.loads(content)

                    # 如果是 dict，尝试在常见字段中取出列表
                    if isinstance(parsed, dict):
                        for key in ['data', 'candles', 'klines', 'records']:
                            if key in parsed and isinstance(parsed[key], list):
                                parsed = parsed[key]
                                break
                    # 如果是列表，直接转 DataFrame
                    if isinstance(parsed, list):
                        df = pd.DataFrame(parsed)
                    else:
                        # 回退到 pandas 解析（可兼容 JSONL）
                        df = pd.read_json(file_path, lines=True)
                except Exception:
                    # 回退到 pandas 解析（可兼容 JSONL）
                    df = pd.read_json(file_path, lines=True)
            else:
                print(f"❌ 不支持的文件格式: {path.suffix}")
                return None
            
            # 检查必需的列
            # 支持多种常见字段名称
            column_mapping = {
                't': 'timestamp',
                'time': 'timestamp',
                'o': 'open',
                'h': 'high',
                'l': 'low',
                'c': 'close',
                'v': 'volume',
                'vol': 'volume'
            }
            for src, dst in column_mapping.items():
                if src in df.columns and dst not in df.columns:
                    df = df.rename(columns={src: dst})

            required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                print(f"❌ 文件缺少必需的列: {missing_cols}")
                print(f"   现有列: {df.columns.tolist()}")
                return None
            
            # 转换timestamp为datetime
            if df['timestamp'].dtype == 'object':
                # 尝试多种格式
                try:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                except:
                    # 如果是毫秒时间戳
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            elif df['timestamp'].dtype in ['int64', 'float64']:
                # 假设是毫秒时间戳
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            # 确保数值列为float
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # 按时间排序
            df = df.sort_values('timestamp').reset_index(drop=True)
            
            # 只保留需要的列
            df = df[required_cols]
            
            # 移除空值
            df = df.dropna()
            
            symbol_str = f" {symbol}" if symbol else ""
            print(f"✅ 加载完成{symbol_str}: {len(df)} 条K线数据")
            print(f"   时间范围: {df['timestamp'].min()} 至 {df['timestamp'].max()}")
            
            return df
            
        except Exception as e:
            print(f"❌ 从文件加载数据失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def load(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        data_file: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        加载历史数据（统一接口）
        
        Args:
            symbol: 交易对符号
            timeframe: 时间周期（仅用于API数据源）
            start_date: 开始时间（仅用于API数据源）
            end_date: 结束时间（仅用于API数据源）
            data_file: 本地数据文件路径（如果提供则优先使用）
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        # 优先使用本地文件
        if data_file:
            return self.load_from_file(data_file, symbol)
        
        # 使用API加载
        if not start_date or not end_date:
            raise ValueError("使用API数据源时必须提供 start_date 和 end_date")
        
        return self.load_from_api(symbol, timeframe, start_date, end_date)
