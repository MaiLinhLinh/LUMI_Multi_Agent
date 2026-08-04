from rag_manager.presentation.speech_text import alignment_text, derive_speech_text, spoken_text


def test_spoken_text_expands_weather_numbers_and_units() -> None:
    result = spoken_text("Xác suất mưa là 96%, nhiệt độ 30.4°C và gió 1.65 m/s.")

    assert result == (
        "Xác suất mưa là chín mươi sáu phần trăm, nhiệt độ ba mươi phẩy bốn độ C "
        "và gió một phẩy sáu năm mét trên giây."
    )


def test_spoken_text_expands_dates_before_generic_numbers() -> None:
    result = spoken_text("Đỉnh mưa vào ngày 31 tháng 7 năm 2026, tức 2026-07-31.")

    assert result == (
        "Đỉnh mưa vào ngày ba mươi mốt tháng bảy năm hai nghìn không trăm hai mươi sáu, "
        "tức ngày ba mươi mốt tháng bảy năm hai nghìn không trăm hai mươi sáu."
    )


def test_spoken_text_expands_compact_day_month_dates() -> None:
    result = spoken_text("Mưa cao nhất vào 05/08; lượng mưa lớn vào 06/08/2026.")

    assert result == (
        "Mưa cao nhất vào ngày năm tháng tám; lượng mưa lớn vào ngày sáu tháng tám "
        "năm hai nghìn không trăm hai mươi sáu."
    )


def test_alignment_text_is_ascii_lowercase_and_punctuation_free() -> None:
    spoken, aligned = derive_speech_text("Nhiệt độ: 25.2–29.9°C; mưa 96%!")

    assert spoken == "Nhiệt độ: hai mươi lăm phẩy hai–hai mươi chín phẩy chín độ C; mưa chín mươi sáu phần trăm!"
    assert aligned == "nhiet do hai muoi lam phay hai hai muoi chin phay chin do xe mua chin muoi sau phan tram"
    assert alignment_text("Độ ẩm") == "do am"


def test_spoken_text_expands_hourly_weather_times() -> None:
    spoken, aligned = derive_speech_text("Lúc 14:00 nhiệt độ là 30.4°C; đến 18:30 có mưa.")

    assert spoken == (
        "Lúc mười bốn giờ nhiệt độ là ba mươi phẩy bốn độ C; "
        "đến mười tám giờ ba mươi phút có mưa."
    )
    assert aligned == (
        "luc muoi bon gio nhiet do la ba muoi phay bon do xe den muoi tam gio ba muoi phut co mua"
    )
