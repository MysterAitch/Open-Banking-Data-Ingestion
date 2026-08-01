from datetime import date

from obdi.identity import content_key, normalise_description


def key(description: str, *, amount: int = -1499, account: str = "acct-1") -> str:
    return content_key(
        amount_minor=amount,
        value_date=date(2026, 3, 14),
        description=description,
    )


class TestDescriptionNormalisation:
    def test_Description_WhenCaseDiffers_NormalisesIdentically(self):
        assert normalise_description("TESCO STORES") == normalise_description("Tesco Stores")

    def test_Description_WhenPunctuationDiffers_NormalisesIdentically(self):
        assert normalise_description("M&S, LONDON") == normalise_description("M S LONDON")

    def test_Description_WhenEmbeddedDatePresent_DateRemoved(self):
        # The same payment seen twice can carry different embedded dates.
        assert normalise_description("TESCO ON 14/03/2026") == normalise_description("TESCO")

    def test_Description_WhenCardLastFourPresent_Removed(self):
        assert normalise_description("TESCO CARD 4912") == normalise_description("TESCO")

    def test_Description_WhenPendingMarkerPresent_Removed(self):
        # The marker vanishes when the transaction settles, so it must not
        # participate in identity.
        assert normalise_description("PENDING TESCO") == normalise_description("TESCO")

    def test_Description_WhenWhitespaceDiffers_NormalisesIdentically(self):
        assert normalise_description("  TESCO   STORES ") == normalise_description("TESCO STORES")


class TestContentKey:
    def test_Transaction_WhenSamePaymentDescribedSlightlyDifferently_HashesIdentically(self):
        assert key("Tesco Stores CARD 4912 ON 14/03/2026") == key("Tesco Stores")

    def test_Transaction_WhenDescriptionHasBareTrailingDigits_NotStrippedSoHashesDiffer(self):
        # Deliberately conservative. Stripping every bare four-digit group would
        # also erase meaningful ones (store numbers, years, quantities) and
        # collapse genuinely different transactions into one.
        #
        # Under-normalising is the safe direction: a missed match falls through
        # to the fuzzy tier and then to human review, whereas an over-eager
        # match silently merges two real payments and is very hard to spot.
        assert key("TESCO STORES 4912") != key("TESCO STORES")

    def test_Transaction_WhenAmountDiffers_HashesDifferently(self):
        assert key("TESCO", amount=-1499) != key("TESCO", amount=-1500)

    def test_Transaction_WhenDifferentAccount_HashesIdentically(self):
        # Deliberate, and the point of the design: which account a payment
        # belongs to is the one revisable fact in the system, so it must not
        # be inside the hash - re-binding would invalidate every stored key.
        # Cross-account safety lives in matching, which filters to same-account
        # candidates before any key is compared.
        assert key("TESCO", account="acct-1") == key("TESCO", account="acct-2")

    def test_Transaction_WhenMerchantDiffers_HashesDifferently(self):
        assert key("TESCO") != key("SAINSBURYS")

    def test_Transaction_WhenValueDateDiffers_HashesDifferently(self):
        a = content_key(amount_minor=-100, value_date=date(2026, 3, 14), description="X")
        b = content_key(amount_minor=-100, value_date=date(2026, 3, 15), description="X")
        assert a != b
