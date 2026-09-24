"""生产配置校验（V3 Wave D-a / J5）。

两条测试纪律：

1. **不碰全局单例。** `app.core.config.settings` 是模块级实例，被半个代码库导入。
   这里一律显式构造 `Settings(...)`，任何一次污染都会以「另一个测试莫名其妙失败」
   的形式出现，而且极难定位。
2. **构造时切断 `.env` 与进程环境。** `_env_file=None` 关掉 dotenv；四个受测字段
   每次都显式传入（init 参数在 pydantic-settings 里优先级最高，压过环境变量），
   否则这些用例在开发者机器上和 CI 上会给出不同结论。
"""

import pytest
from pydantic import ValidationError

from app.core.config import (
    INSECURE_SECRET_KEY_DEFAULT,
    MIN_SECRET_KEY_LENGTH,
    Settings,
)

# 长度足够、且明显不是默认值的密钥
VALID_SECRET = "a" * MIN_SECRET_KEY_LENGTH
VALID_ORIGINS = "https://quantbot.example.com"


def build_settings(**overrides: object) -> Settings:
    """构造一份与本机 .env / 环境变量隔离的配置。"""
    params: dict[str, object] = {
        "environment": "production",
        "secret_key": VALID_SECRET,
        "allowed_origins": VALID_ORIGINS,
        "debug": False,
    }
    params.update(overrides)
    return Settings(_env_file=None, **params)  # type: ignore[arg-type]


class TestProductionRejectsInsecureConfig:
    def test_default_secret_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(secret_key=INSECURE_SECRET_KEY_DEFAULT)

        assert "SECRET_KEY" in str(exc_info.value)

    def test_rejection_message_tells_you_how_to_fix_it(self) -> None:
        # 「Invalid configuration」这种报错只会让人去翻源码；必须给出可执行命令。
        with pytest.raises(ValidationError) as exc_info:
            build_settings(secret_key=INSECURE_SECRET_KEY_DEFAULT)

        message = str(exc_info.value)
        assert "gen-secret" in message
        assert "openssl rand" in message

    def test_env_example_placeholder_is_rejected_regardless_of_length(self) -> None:
        # .env.example 里的占位串与代码默认值并不相同。照抄 .env.example 上生产
        # 是最常见的一条路径，不能只靠「长度恰好不够」拦住。
        long_placeholder = "CHANGE_ME_RUN_make_gen-secret_" + "0" * 40
        with pytest.raises(ValidationError) as exc_info:
            build_settings(secret_key=long_placeholder)

        assert "SECRET_KEY" in str(exc_info.value)

    def test_short_secret_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(secret_key="short-but-not-the-default")

        assert str(MIN_SECRET_KEY_LENGTH) in str(exc_info.value)

    def test_error_message_never_contains_the_secret_value(self) -> None:
        # 报错会进容器日志/告警系统。哪怕是「当前值是 xxx」也不行。
        leaky = "zzz-my-real-production-secret-zzz"
        with pytest.raises(ValidationError) as exc_info:
            build_settings(secret_key=leaky, debug=True)

        assert leaky not in str(exc_info.value)

    def test_wildcard_origin_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(allowed_origins="https://quantbot.example.com,*")

        assert "ALLOWED_ORIGINS" in str(exc_info.value)

    def test_empty_origins_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(allowed_origins="")

        assert "ALLOWED_ORIGINS" in str(exc_info.value)

    def test_debug_true_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            build_settings(debug=True)

        assert "DEBUG" in str(exc_info.value)

    def test_all_problems_are_reported_at_once(self) -> None:
        # 一次只报一条会让人「改一条、重启、再撞下一条」，在生产发布窗口里代价很高。
        with pytest.raises(ValidationError) as exc_info:
            build_settings(
                secret_key=INSECURE_SECRET_KEY_DEFAULT,
                allowed_origins="*",
                debug=True,
            )

        message = str(exc_info.value)
        assert "SECRET_KEY" in message
        assert "ALLOWED_ORIGINS" in message
        assert "DEBUG" in message

    def test_valid_production_config_is_accepted(self) -> None:
        settings = build_settings()

        assert settings.is_production is True
        assert settings.origins_list == [VALID_ORIGINS]


class TestNonProductionStaysZeroConfig:
    """开发环境必须零配置可启动。

    把开发也卡住的话，人只会去改 ENVIRONMENT 绕过 —— 这个控制反而彻底失效。
    """

    def test_development_boots_with_no_configuration_at_all(self) -> None:
        settings = Settings(_env_file=None, environment="development")

        assert settings.secret_key == INSECURE_SECRET_KEY_DEFAULT
        assert settings.is_production is False

    @pytest.mark.parametrize("environment", ["development", "staging"])
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("secret_key", INSECURE_SECRET_KEY_DEFAULT),
            ("secret_key", "tiny"),
            ("allowed_origins", "*"),
            ("allowed_origins", ""),
            ("debug", True),
        ],
    )
    def test_insecure_values_are_allowed_outside_production(
        self, environment: str, field: str, value: object
    ) -> None:
        settings = build_settings(environment=environment, **{field: value})

        assert settings.environment == environment

    def test_module_level_singleton_imports_without_exploding(self) -> None:
        # settings = Settings() 在 config.py 末尾执行。开发机上导入 app.core.config
        # 一旦抛异常，整个后端连测试都跑不起来。
        from app.core.config import settings

        assert settings.app_name == "QuantBot"


class TestDefaultConstantIsSingleSource:
    def test_field_default_is_the_constant(self) -> None:
        # 字段默认值与校验必须引用同一个常量：各写一份字面量的话，
        # 改了一处忘了另一处，校验就静默失效了。
        assert Settings.model_fields["secret_key"].default is INSECURE_SECRET_KEY_DEFAULT
