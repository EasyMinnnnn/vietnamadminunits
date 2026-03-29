from __future__ import annotations

"""Pandas utilities for vietnamadminunits.

This module provides helper functions to standardize and convert
Vietnamese administrative unit columns in a pandas ``DataFrame``.

The implementation here is based off of the upstream project but adds
several improvements:

* **Graceful error handling** – when an address is missing or cannot
  be parsed/converted, a sentinel value (default ``"Lỗi định dạng"``)
  is returned instead of raising an exception.  This allows batch
  processing to continue without interrupting the user interface.
* **Optional error value** – callers can override the default
  ``error_value`` string to suit their needs.

These changes enable better batch-processing performance and give
end‑users immediate feedback in the downloaded results file rather
than as unhelpful exceptions on the web page.
"""

from ..parser import parse_address, ParseMode
from ..converter import convert_address, ConvertMode
import warnings
from typing import Union, Optional
from tqdm import tqdm
import pandas as pd  # type: ignore


def standardize_admin_unit_columns(
    df: pd.DataFrame,
    province: str,
    district: Optional[str] = None,
    ward: Optional[str] = None,
    parse_mode: Union[str, ParseMode] = ParseMode.latest(),
    convert_mode: Union[str, ConvertMode, None] = None,
    inplace: bool = False,
    prefix: str = "standardized_",
    suffix: str = "",
    short_name: bool = True,
    show_progress: bool = True,
) -> pd.DataFrame:
    """Standardizes administrative unit columns in a DataFrame.

    Given a DataFrame with province, district and ward columns this
    function parses each row into an ``AdminUnit`` and then populates
    standardized columns.  When ``convert_mode`` is provided the data
    is converted from an old format (e.g. 63 province) into the new
    2025 format.  Otherwise the selected ``parse_mode`` controls how
    the administrative unit is parsed.

    Args:
        df: Input DataFrame.
        province: Name of the province column.  Required.
        district: Name of the district column.  Optional.
        ward: Name of the ward column.  Optional.
        parse_mode: One of the :class:`ParseMode` values.  Defaults to
            ``ParseMode.latest()``.
        convert_mode: One of the :class:`ConvertMode` values.  When
            provided, conversion mode takes precedence over parsing
            mode.
        inplace: When ``True`` the original columns are replaced;
            otherwise new columns prefixed with ``prefix`` and suffixed
            with ``suffix`` are inserted.
        prefix: Prefix to add to the new column names when
            ``inplace`` is ``False``.
        suffix: Suffix to add to the new column names when
            ``inplace`` is ``False``.
        short_name: Whether to use short or full names for
            administrative unit fields.
        show_progress: Whether to display a progress bar via ``tqdm``.

    Returns:
        The DataFrame with standardized administrative unit columns.
    """
    # Ensure we don't mutate the caller's DataFrame
    df = df.copy()
    admin_unit_columns = [c for c in [ward, district, province] if c]

    # Validate input
    if not province:
        raise ValueError("The name of the province column must be provided")

    if convert_mode:
        # When converting, district and ward may be optional but warn
        if not district or not ward:
            warnings.warn(
                "The names of the District or Ward columns are not provided. "
                "Therefore, only the Province level will be converted.",
                UserWarning,
            )
    else:
        # parse_mode restrictions
        if parse_mode in [ParseMode.FROM_2025, ParseMode.FROM_2025.value] and district:
            warnings.warn(
                "FROM_2025 mode is not supported with the district level.",
                UserWarning,
            )
        if parse_mode in [ParseMode.LEGACY, ParseMode.LEGACY.value] and ward and not district:
            raise ValueError(
                "The name of the district column must be provided in order to parse the ward data."
            )

    original_columns = df.columns.tolist()

    # Build an address string used as a merge key.  We prepend a comma
    # so that missing components still generate unique values.
    df["address"] = ""
    for column in admin_unit_columns:
        df["address"] += "," + df[column].fillna("")
    df_address = df[["address"]].drop_duplicates()

    # Define parser or converter for unique addresses
    if convert_mode:
        parser = lambda x: convert_address(address=x, mode=convert_mode)  # type: ignore
    else:
        if parse_mode in [ParseMode.FROM_2025, ParseMode.FROM_2025.value]:
            level = 2 if ward else 1
        elif parse_mode in [ParseMode.LEGACY, ParseMode.LEGACY.value]:
            level = 3 if ward else (2 if district else 1)
        parser = lambda x: parse_address(address=x, mode=parse_mode, level=level, keep_street=False)  # type: ignore

    # Apply parser/converter to unique addresses
    if show_progress:
        tqdm.pandas(desc="Standardizing unique administrative units")
        df_address["admin_unit"] = df_address["address"].progress_apply(parser)
    else:
        df_address["admin_unit"] = df_address["address"].apply(parser)

    # Extract desired attributes
    for col_type, col_name in zip(["province", "district", "ward"], [province, district, ward]):
        if not col_name:
            continue
        # Skip district when converting
        if col_type == "district" and convert_mode:
            continue
        attr = f"{'short_' if short_name else ''}{col_type}"
        target_col = col_name if inplace else f"{prefix}{col_name}{suffix}"
        df_address[target_col] = df_address["admin_unit"].apply(lambda x: getattr(x, attr) if x else None)

    # Merge back to the original DataFrame
    if inplace:
        df.drop(columns=admin_unit_columns, inplace=True, errors="ignore")

    df = df.merge(df_address.drop(columns=["admin_unit"]), on="address", how="left")
    df.drop(columns=["address"], inplace=True)

    # Preserve original column order when inplace
    if inplace:
        original_columns = [col for col in original_columns if col in df.columns]
        df = df[original_columns]
    return df


def convert_address_column(
    df: pd.DataFrame,
    address: str,
    convert_mode: Union[str, ConvertMode] = ConvertMode.CONVERT_2025,
    inplace: bool = False,
    prefix: str = "converted_",
    suffix: str = "",
    short_name: bool = True,
    show_progress: bool = True,
    error_value: str = "Lỗi định dạng",
) -> pd.DataFrame:
    """Convert an address column in a DataFrame.

    Each unique address in the specified column is converted using
    :func:`convert_address`.  The resulting standardized address is
    returned as a new column.  Addresses that are empty or raise an
    exception during conversion are replaced with ``error_value``.

    Args:
        df: The input DataFrame.
        address: Name of the address column.
        convert_mode: Conversion mode (default 'CONVERT_2025').
        inplace: Whether to replace the original column.  When ``True``
            the original address column is dropped and the converted
            values take its place.
        prefix: Prefix for the new column when ``inplace`` is ``False``.
        suffix: Suffix for the new column when ``inplace`` is ``False``.
        short_name: Whether to use short administrative unit names.
        show_progress: Whether to display a progress bar via ``tqdm``.
        error_value: Sentinel value to use when an address cannot be
            converted.

    Returns:
        The DataFrame with the converted address column.
    """
    # Make a copy so we don't mutate the caller's DataFrame
    df = df.copy()
    original_columns = df.columns.tolist()

    # Distinct addresses to avoid redundant conversions
    df_address = df[[address]].drop_duplicates()

    def convert_and_get_address(x: str) -> str:
        """Helper to convert a single address safely.

        Returns ``error_value`` if the input is empty/blank or if
        ``convert_address`` raises an exception.
        """
        try:
            # Treat missing/empty values as invalid
            if not isinstance(x, str) or not x.strip():
                return error_value
            admin_unit = convert_address(address=x, mode=convert_mode)  # type: ignore
            # Some implementations may return None to indicate failure
            if admin_unit is None:
                return error_value
            return admin_unit.get_address(short_name=short_name)
        except Exception:
            return error_value

    # Convert addresses
    if show_progress:
        tqdm.pandas(desc="Converting unique addresses")
        df_address["new_address"] = df_address[address].fillna("").progress_apply(convert_and_get_address)
    else:
        df_address["new_address"] = df_address[address].fillna("").apply(convert_and_get_address)

    # Merge back to original DataFrame
    df = df.merge(df_address, on=address, how="left")

    if inplace:
        df.drop(columns=[address], inplace=True)
        df.rename(columns={"new_address": address}, inplace=True)
        # Maintain original column order when dropping/replacing
        df = df[[col for col in original_columns]]
    else:
        df.rename(columns={"new_address": f"{prefix}{address}{suffix}"}, inplace=True)

    return df
