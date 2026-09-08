from typing import NamedTuple


class BarcodeModule(NamedTuple):
    is_black: bool
    is_guard: bool


class Ean13Result(NamedTuple):
    barcode_value: str
    formatted_display: str
    modules: list[BarcodeModule]  # 95 modules
    total_modules: int


class BarcodeGeneratorService:
    """
    Independent EAN-13 bar matrix calculator.
    Produces raw 95-module binary representations with guard bar identification.
    Pure python, zero external dependencies, 100% vector-ready.
    """

    L_CODE = {
        "0": "0001101",
        "1": "0011001",
        "2": "0010011",
        "3": "0111101",
        "4": "0100011",
        "5": "0110001",
        "6": "0101111",
        "7": "0111011",
        "8": "0110111",
        "9": "0001011",
    }

    G_CODE = {
        "0": "0100111",
        "1": "0110011",
        "2": "0011011",
        "3": "0100001",
        "4": "0011101",
        "5": "0111001",
        "6": "0000101",
        "7": "0010001",
        "8": "0001001",
        "9": "0010111",
    }

    R_CODE = {
        "0": "1110010",
        "1": "1100110",
        "2": "1101100",
        "3": "1000010",
        "4": "1011100",
        "5": "1001110",
        "6": "1010000",
        "7": "1000100",
        "8": "1001000",
        "9": "1110100",
    }

    PARITIES = {
        "0": "AAAAAA",
        "1": "AABABB",
        "2": "AABBAB",
        "3": "AABBBA",
        "4": "ABAABB",
        "5": "ABBAAB",
        "6": "ABBBAA",
        "7": "ABABAB",
        "8": "ABABBA",
        "9": "ABBABA",
    }

    @classmethod
    def calculate_checksum(cls, digits_12: str) -> str:
        """Calculates EAN-13 check digit for 12 digits."""
        if len(digits_12) != 12 or not digits_12.isdigit():
            raise ValueError(f"12 ta raqam talab qilinadi: {digits_12}")
        total = 0
        for idx, char in enumerate(digits_12):
            val = int(char)
            total += val if idx % 2 == 0 else val * 3
        checksum = (10 - (total % 10)) % 10
        return str(checksum)

    @classmethod
    def normalize_ean13(cls, value: str) -> str:
        """
        Validates or normalizes value into a 13-digit EAN-13 string.
        If 12 digits, appends valid checksum.
        If 13 digits, validates checksum.
        """
        digits = "".join(c for c in str(value) if c.isdigit())
        if len(digits) == 12:
            return digits + cls.calculate_checksum(digits)
        elif len(digits) == 13:
            expected = cls.calculate_checksum(digits[:12])
            if digits[12] != expected:
                # Return normalized with valid checksum
                return digits[:12] + expected
            return digits
        elif len(digits) < 12:
            # Pad left with zeros to 12 digits then append checksum
            padded = digits.zfill(12)
            return padded + cls.calculate_checksum(padded)
        else:
            # Truncate to 12 + checksum
            truncated = digits[:12]
            return truncated + cls.calculate_checksum(truncated)

    @classmethod
    def generate(cls, raw_value: str) -> Ean13Result:
        """
        Generates 95-module EAN-13 pattern with guard bar indicators.
        """
        ean = cls.normalize_ean13(raw_value)
        first_digit = ean[0]
        left_digits = ean[1:7]
        right_digits = ean[7:13]

        parity_pattern = cls.PARITIES[first_digit]

        modules: list[BarcodeModule] = []

        # 1. Left Guard (101)
        for bit in "101":
            modules.append(BarcodeModule(is_black=(bit == "1"), is_guard=True))

        # 2. Left 6 digits
        for digit, parity in zip(left_digits, parity_pattern):
            pattern = cls.L_CODE[digit] if parity == "A" else cls.G_CODE[digit]
            for bit in pattern:
                modules.append(BarcodeModule(is_black=(bit == "1"), is_guard=False))

        # 3. Center Guard (01010)
        for bit in "01010":
            modules.append(BarcodeModule(is_black=(bit == "1"), is_guard=True))

        # 4. Right 6 digits (always R_CODE)
        for digit in right_digits:
            pattern = cls.R_CODE[digit]
            for bit in pattern:
                modules.append(BarcodeModule(is_black=(bit == "1"), is_guard=False))

        # 5. Right Guard (101)
        for bit in "101":
            modules.append(BarcodeModule(is_black=(bit == "1"), is_guard=True))

        formatted_display = f"{first_digit} {left_digits} {right_digits}"

        return Ean13Result(
            barcode_value=ean,
            formatted_display=formatted_display,
            modules=modules,
            total_modules=len(modules),  # 95
        )
