//! `snes_kit._native`: thin PyO3 wrappers. All behaviour lives in the `snes-kit` crate.

use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use snes_kit::addr::{self, Mapping};
use snes_kit::{bps, hal, header, smw};

create_exception!(
    _native,
    RomError,
    PyValueError,
    "The ROM, patch or data is not what was expected."
);

fn err(e: snes_kit::Error) -> PyErr {
    RomError::new_err(e.0)
}

fn mapping(name: &str) -> PyResult<Mapping> {
    name.parse().map_err(err)
}

fn bytes<'py>(py: Python<'py>, data: &[u8]) -> Bound<'py, PyBytes> {
    PyBytes::new(py, data)
}

#[pyfunction]
fn crc32(data: &[u8]) -> u32 {
    snes_kit::crc32::crc32(data)
}

#[pyfunction]
fn parse_address(text: &str) -> PyResult<u32> {
    addr::parse_address(text).map_err(err)
}

#[pyfunction]
#[pyo3(signature = (address, mapping = "lorom"))]
fn to_offset(address: u32, mapping: &str) -> PyResult<u32> {
    addr::to_offset(address, self::mapping(mapping)?).map_err(err)
}

#[pyfunction]
#[pyo3(signature = (offset, mapping = "lorom", fast = false))]
fn to_address(offset: u32, mapping: &str, fast: bool) -> PyResult<u32> {
    Ok(addr::to_address(offset, self::mapping(mapping)?, fast))
}

#[pyfunction]
fn detect_mapping(rom: &[u8]) -> &'static str {
    header::detect_mapping(rom).name()
}

#[pyfunction]
fn code_mapping(rom: &[u8]) -> &'static str {
    header::code_mapping(rom).name()
}

#[pyfunction]
fn header_offset(mapping: &str) -> PyResult<usize> {
    Ok(header::header_offset(self::mapping(mapping)?))
}

#[pyfunction]
fn checksum(rom: &[u8]) -> u16 {
    header::checksum(rom)
}

#[pyfunction]
#[pyo3(signature = (rom, mapping = None))]
fn fix_checksum<'py>(
    py: Python<'py>,
    rom: &[u8],
    mapping: Option<&str>,
) -> PyResult<Bound<'py, PyBytes>> {
    let mapping = mapping.map(self::mapping).transpose()?;
    Ok(bytes(py, &header::fix_checksum(rom, mapping).map_err(err)?))
}

#[pyfunction]
fn strip_copier_header<'py>(py: Python<'py>, data: &[u8]) -> Bound<'py, PyBytes> {
    bytes(py, header::strip_copier_header(data))
}

#[pyfunction]
#[pyo3(signature = (source, target, metadata = ""))]
fn bps_make<'py>(
    py: Python<'py>,
    source: &[u8],
    target: &[u8],
    metadata: &str,
) -> Bound<'py, PyBytes> {
    let patch = py.detach(|| bps::make(source, target, metadata));
    bytes(py, &patch)
}

#[pyfunction]
fn bps_apply<'py>(py: Python<'py>, source: &[u8], patch: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let out = py.detach(|| bps::apply(source, patch)).map_err(err)?;
    Ok(bytes(py, &out))
}

#[pyfunction]
fn hal_unpack<'py>(py: Python<'py>, data: &[u8]) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    let (out, used) = hal::unpack(data).map_err(err)?;
    Ok((bytes(py, &out), used))
}

#[pyfunction]
#[pyo3(signature = (data, fast = false, optimal = false))]
fn hal_pack<'py>(
    py: Python<'py>,
    data: &[u8],
    fast: bool,
    optimal: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let out = py
        .detach(|| hal::pack(data, hal::Options { fast, optimal }))
        .map_err(err)?;
    Ok(bytes(py, &out))
}

#[pyfunction]
fn smw_layer1<'py>(
    py: Python<'py>,
    objects: Vec<(i32, i32, u8, u8)>,
) -> PyResult<Bound<'py, PyBytes>> {
    let objects: Vec<smw::Object> = objects
        .into_iter()
        .map(|(x, y, number, settings)| smw::Object {
            x,
            y,
            number,
            settings,
        })
        .collect();
    Ok(bytes(py, &smw::layer1(&objects).map_err(err)?))
}

#[pyfunction]
fn smw_sprites<'py>(
    py: Python<'py>,
    header: u8,
    entries: Vec<(i32, i32, u8)>,
) -> PyResult<Bound<'py, PyBytes>> {
    let entries: Vec<smw::Sprite> = entries
        .into_iter()
        .map(|(x, y, number)| smw::Sprite { x, y, number })
        .collect();
    Ok(bytes(py, &smw::sprites(header, &entries).map_err(err)?))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("RomError", m.py().get_type::<RomError>())?;
    m.add("HAL_DATA_SIZE", hal::DATA_SIZE)?;
    m.add("COPIER_HEADER", header::COPIER_HEADER)?;
    m.add_function(wrap_pyfunction!(crc32, m)?)?;
    m.add_function(wrap_pyfunction!(parse_address, m)?)?;
    m.add_function(wrap_pyfunction!(to_offset, m)?)?;
    m.add_function(wrap_pyfunction!(to_address, m)?)?;
    m.add_function(wrap_pyfunction!(detect_mapping, m)?)?;
    m.add_function(wrap_pyfunction!(code_mapping, m)?)?;
    m.add_function(wrap_pyfunction!(header_offset, m)?)?;
    m.add_function(wrap_pyfunction!(checksum, m)?)?;
    m.add_function(wrap_pyfunction!(fix_checksum, m)?)?;
    m.add_function(wrap_pyfunction!(strip_copier_header, m)?)?;
    m.add_function(wrap_pyfunction!(bps_make, m)?)?;
    m.add_function(wrap_pyfunction!(bps_apply, m)?)?;
    m.add_function(wrap_pyfunction!(hal_unpack, m)?)?;
    m.add_function(wrap_pyfunction!(hal_pack, m)?)?;
    m.add_function(wrap_pyfunction!(smw_layer1, m)?)?;
    m.add_function(wrap_pyfunction!(smw_sprites, m)?)?;
    Ok(())
}
