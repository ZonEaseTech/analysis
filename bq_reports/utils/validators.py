#!/usr/bin/env python3
"""
数据校验框架 (Data Validation Framework)

提供多维度数据校验能力，支持：
1. 内部一致性校验（总=分项和）
2. 数值范围校验（无负值、比例合理）
3. 跨源比对校验（Excel vs BigQuery）
4. 时间连续性校验

Usage:
    from utils.validators import (
        ValidationResult, ConsistencyValidator, 
        RangeValidator, CrossSourceValidator
    )
    
    # 创建校验链
    validators = [
        ConsistencyValidator(total_field='total', sum_fields=['a', 'b']),
        RangeValidator(field='amount', min_val=0),
        CrossSourceValidator(sample_count=5)
    ]
    
    # 执行校验
    for validator in validators:
        result = validator.validate(data)
        if not result.is_valid:
            print(result.errors)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
import random


@dataclass
class ValidationError:
    """校验错误详情"""
    rule: str  # 校验规则名称
    message: str  # 错误描述
    details: Dict[str, Any] = field(default_factory=dict)  # 详细信息


@dataclass
class ValidationResult:
    """校验结果"""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def success(cls, stats: Dict[str, Any] = None) -> "ValidationResult":
        return cls(is_valid=True, stats=stats or {})
    
    @classmethod
    def failure(cls, errors: List[ValidationError], stats: Dict[str, Any] = None) -> "ValidationResult":
        return cls(is_valid=False, errors=errors, stats=stats or {})
    
    def merge(self, other: "ValidationResult") -> "ValidationResult":
        """合并两个校验结果"""
        return ValidationResult(
            is_valid=self.is_valid and other.is_valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
            stats={**self.stats, **other.stats}
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "is_valid": self.is_valid,
            "errors": [{"rule": e.rule, "message": e.message, "details": e.details} for e in self.errors],
            "warnings": [{"rule": w.rule, "message": w.message, "details": w.details} for w in self.warnings],
            "stats": self.stats
        }


class DataValidator(ABC):
    """数据校验器基类"""
    
    @abstractmethod
    def validate(self, data: Any) -> ValidationResult:
        """执行校验，返回结果"""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """校验器名称"""
        pass


class ConsistencyValidator(DataValidator):
    """
    内部一致性校验器
    
    验证：总字段 = 分项字段之和
    
    Example:
        validator = ConsistencyValidator(
            total_field='total_turnover',
            sum_fields=['takeout_turnover', 'non_takeout_turnover'],
            tolerance=0.01  # 允许0.01的浮点误差
        )
    """
    
    def __init__(
        self,
        total_field: str,
        sum_fields: List[str],
        tolerance: float = 0.01,
        name: str = "consistency"
    ):
        self.total_field = total_field
        self.sum_fields = sum_fields
        self.tolerance = tolerance
        self._name = name
    
    @property
    def name(self) -> str:
        return self._name
    
    def validate(self, data: List[Dict[str, Any]]) -> ValidationResult:
        """
        校验数据列表的一致性
        
        Args:
            data: 数据字典列表
        """
        errors = []
        stats = {"checked": len(data), "mismatched": 0}
        
        for idx, row in enumerate(data):
            total = row.get(self.total_field, 0) or 0
            sum_value = sum(row.get(f, 0) or 0 for f in self.sum_fields)
            
            if abs(total - sum_value) > self.tolerance:
                errors.append(ValidationError(
                    rule=self.name,
                    message=f"第{idx+1}行数据不一致: {self.total_field}={total}, 分项和={sum_value:.2f}",
                    details={
                        "row_index": idx,
                        "total_field": self.total_field,
                        "total_value": total,
                        "sum_fields": self.sum_fields,
                        "sum_value": sum_value,
                        "diff": total - sum_value
                    }
                ))
                stats["mismatched"] += 1
        
        if errors:
            return ValidationResult.failure(errors, stats)
        return ValidationResult.success(stats)


class RangeValidator(DataValidator):
    """
    数值范围校验器
    
    验证字段值在指定范围内，支持多字段批量校验
    
    Example:
        validator = RangeValidator(
            rules=[
                {"field": "amount", "min": 0, "name": "无负值"},
                {"field": "ratio", "min": 0, "max": 1, "name": "比例范围"},
            ]
        )
    """
    
    def __init__(self, rules: List[Dict[str, Any]]):
        """
        Args:
            rules: 校验规则列表，每项包含:
                - field: 字段名
                - min: 最小值（可选）
                - max: 最大值（可选）
                - name: 规则名称（可选）
        """
        self.rules = rules
    
    @property
    def name(self) -> str:
        return "range"
    
    def validate(self, data: List[Dict[str, Any]]) -> ValidationResult:
        errors = []
        stats = {"checked": len(data) * len(self.rules), "violations": 0}
        
        for idx, row in enumerate(data):
            for rule in self.rules:
                field = rule["field"]
                value = row.get(field)
                
                if value is None:
                    continue
                
                min_val = rule.get("min")
                max_val = rule.get("max")
                rule_name = rule.get("name", field)
                
                if min_val is not None and value < min_val:
                    errors.append(ValidationError(
                        rule=f"{self.name}:{rule_name}",
                        message=f"第{idx+1}行 {field}={value} 小于最小值 {min_val}",
                        details={"row_index": idx, "field": field, "value": value, "min": min_val}
                    ))
                    stats["violations"] += 1
                
                if max_val is not None and value > max_val:
                    errors.append(ValidationError(
                        rule=f"{self.name}:{rule_name}",
                        message=f"第{idx+1}行 {field}={value} 大于最大值 {max_val}",
                        details={"row_index": idx, "field": field, "value": value, "max": max_val}
                    ))
                    stats["violations"] += 1
        
        if errors:
            return ValidationResult.failure(errors, stats)
        return ValidationResult.success(stats)


class RatioValidator(DataValidator):
    """
    比例校验器
    
    验证子项占父项的比例是否合理
    
    Example:
        validator = RatioValidator(
            parent_field='total_turnover',
            child_field='takeout_turnover',
            min_ratio=0,
            max_ratio=0.9,
            name="外卖占比"
        )
    """
    
    def __init__(
        self,
        parent_field: str,
        child_field: str,
        min_ratio: float = 0,
        max_ratio: float = 1,
        name: str = "ratio"
    ):
        self.parent_field = parent_field
        self.child_field = child_field
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio
        self._name = name
    
    @property
    def name(self) -> str:
        return self._name
    
    def validate(self, data: List[Dict[str, Any]]) -> ValidationResult:
        errors = []
        warnings = []
        stats = {"checked": len(data), "violations": 0}
        
        for idx, row in enumerate(data):
            parent = row.get(self.parent_field, 0) or 0
            child = row.get(self.child_field, 0) or 0
            
            if parent == 0:
                continue
            
            ratio = child / parent
            
            if ratio < self.min_ratio or ratio > self.max_ratio:
                level = "error" if ratio > 1 else "warning"
                error = ValidationError(
                    rule=self.name,
                    message=f"第{idx+1}行 {self.child_field}/{self.parent_field}={ratio:.1%} 超出范围 [{self.min_ratio:.0%}, {self.max_ratio:.0%}]",
                    details={
                        "row_index": idx,
                        "parent": parent,
                        "child": child,
                        "ratio": ratio
                    }
                )
                
                if ratio > 1:
                    errors.append(error)
                else:
                    warnings.append(error)
                stats["violations"] += 1
        
        result = ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            stats=stats
        )
        return result


class CrossSourceValidator(DataValidator):
    """
    跨源比对校验器
    
    抽样对比 Excel 数据与 BigQuery 原始数据
    
    Example:
        from utils.bq_client import get_bq_client
        
        validator = CrossSourceValidator(
            bq_client=get_bq_client(),
            sample_count=5,
            compare_field='total_turnover',
            bq_sql_template="SELECT SUM(amount) FROM `{project}.{dataset}.ttpos_sale_bill` ..."
        )
    """
    
    def __init__(
        self,
        bq_client: Any,
        sample_count: int = 5,
        compare_field: str = "total_turnover",
        tolerance: float = 1.0,
        name: str = "cross_source"
    ):
        self.bq_client = bq_client
        self.sample_count = sample_count
        self.compare_field = compare_field
        self.tolerance = tolerance
        self._name = name
        self.bq_sql_template: Optional[str] = None
    
    @property
    def name(self) -> str:
        return self._name
    
    def set_bq_sql(self, sql_template: str):
        """设置 BigQuery SQL 模板"""
        self.bq_sql_template = sql_template
    
    def validate(
        self, 
        excel_data: List[Dict[str, Any]], 
        merchant_list: List[Tuple[str, str]]
    ) -> ValidationResult:
        """
        抽样校验 Excel 数据与 BQ 数据
        
        Args:
            excel_data: Excel 中的数据
            merchant_list: 商家列表 [(account, uuid), ...]
        """
        from google.cloud import bigquery
        
        if not self.bq_sql_template:
            return ValidationResult.failure([ValidationError(
                rule=self.name,
                message="未设置 BigQuery SQL 模板"
            )])
        
        # 随机抽样
        sample_indices = random.sample(
            range(len(excel_data)), 
            min(self.sample_count, len(excel_data))
        )
        
        errors = []
        stats = {"sampled": len(sample_indices), "mismatched": 0}
        
        for idx in sample_indices:
            excel_row = excel_data[idx]
            account, uuid_str = merchant_list[idx]
            dataset = f"shop{uuid_str}"
            
            try:
                # 查询 BQ
                sql = self.bq_sql_template.format(
                    project=self.bq_client.project,
                    dataset=dataset
                )
                rows = list(self.bq_client.query(sql).result())
                bq_value = float(rows[0][0] or 0) if rows else 0
                
                excel_value = excel_row.get(self.compare_field, 0) or 0
                
                if abs(excel_value - bq_value) > self.tolerance:
                    errors.append(ValidationError(
                        rule=self.name,
                        message=f"门店{excel_row.get('store_no', idx+1)} {self.compare_field} 不一致: Excel={excel_value:.2f}, BQ={bq_value:.2f}",
                        details={
                            "row_index": idx,
                            "dataset": dataset,
                            "excel_value": excel_value,
                            "bq_value": bq_value,
                            "diff": excel_value - bq_value
                        }
                    ))
                    stats["mismatched"] += 1
                    
            except Exception as e:
                errors.append(ValidationError(
                    rule=self.name,
                    message=f"门店{excel_row.get('store_no', idx+1)} BQ查询失败: {str(e)}",
                    details={"row_index": idx, "dataset": dataset, "error": str(e)}
                ))
        
        if errors:
            return ValidationResult.failure(errors, stats)
        return ValidationResult.success(stats)


class ValidationChain:
    """
    校验链 - 按顺序执行多个校验器
    
    Example:
        chain = ValidationChain([
            ConsistencyValidator(...),
            RangeValidator(...),
            RatioValidator(...)
        ])
        
        result = chain.validate(data)
        if not result.is_valid:
            print("校验失败:", result.errors)
    """
    
    def __init__(self, validators: List[DataValidator] = None):
        self.validators = validators or []
    
    def add(self, validator: DataValidator) -> "ValidationChain":
        """添加校验器（链式调用）"""
        self.validators.append(validator)
        return self
    
    def validate(self, data: Any) -> ValidationResult:
        """执行所有校验"""
        final_result = ValidationResult.success()
        
        for validator in self.validators:
            result = validator.validate(data)
            final_result = final_result.merge(result)
        
        return final_result


# 便捷函数
def create_default_validators(
    total_field: str = "total_turnover",
    takeout_field: str = "takeout_turnover",
    non_takeout_field: str = "non_takeout_turnover"
) -> ValidationChain:
    """创建默认校验链（适用于外卖营业额报表）"""
    return ValidationChain([
        # 1. 一致性校验：总 = 外卖 + 非外卖
        ConsistencyValidator(
            total_field=total_field,
            sum_fields=[takeout_field, non_takeout_field]
        ),
        # 2. 范围校验：无负值
        RangeValidator([
            {"field": total_field, "min": 0, "name": "总营业额非负"},
            {"field": takeout_field, "min": 0, "name": "外卖营业额非负"},
        ]),
        # 3. 比例校验：外卖占比 <= 100%
        RatioValidator(
            parent_field=total_field,
            child_field=takeout_field,
            min_ratio=0,
            max_ratio=1,
            name="外卖占比"
        )
    ])
