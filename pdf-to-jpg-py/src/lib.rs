use pdfium_render::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::io::Cursor;
use std::path::PathBuf;

fn find_pdfium_dir() -> Option<PathBuf> {
    if let Ok(dir) = std::env::var("PDFIUM_LIB_DIR") {
        return Some(PathBuf::from(dir));
    }
    None
}

fn find_pdfium() -> Result<Pdfium, PdfiumError> {
    let mut search_paths: Vec<String> = Vec::new();

    if let Some(lib_dir) = find_pdfium_dir() {
        search_paths.push(lib_dir.to_string_lossy().to_string());
    }

    search_paths.extend([
        "./".to_string(),
        "/usr/lib".to_string(),
        "/usr/local/lib".to_string(),
        "/usr/lib/x86_64-linux-gnu".to_string(),
    ]);

    let mut last_err = None;
    for path in &search_paths {
        match Pdfium::bind_to_library(Pdfium::pdfium_platform_library_name_at_path(path)) {
            Ok(bindings) => return Ok(Pdfium::new(bindings)),
            Err(e) => last_err = Some(e),
        }
    }

    match Pdfium::bind_to_system_library() {
        Ok(bindings) => Ok(Pdfium::new(bindings)),
        Err(e) => Err(last_err.unwrap_or(e)),
    }
}

/// Convert a PDF file to JPEG images, saving them to the output directory.
///
/// Args:
///     pdf_path: Path to the input PDF file.
///     output_dir: Directory to save the JPEG images.
///     dpi: Resolution in DPI (default: 200).
///     width: Target width in pixels (default: 2000).
///     max_height: Maximum height in pixels (default: 4000).
///     quality: JPEG quality 1-100 (default: 90).
///
/// Returns:
///     List of paths to the generated JPEG files.
#[pyfunction]
#[pyo3(signature = (pdf_path, output_dir, dpi=200.0, width=2000, max_height=4000, quality=90))]
fn convert_pdf_to_images(
    pdf_path: &str,
    output_dir: &str,
    dpi: f32,
    width: i32,
    max_height: i32,
    quality: u8,
) -> PyResult<Vec<String>> {
    let _ = dpi;
    let output_dir = PathBuf::from(output_dir);
    std::fs::create_dir_all(&output_dir)
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to create output dir: {}", e)))?;

    let pdfium = find_pdfium()
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to load pdfium library: {}. Make sure libpdfium.so is available.", e)))?;

    let document = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to load PDF: {}", e)))?;

    let render_config = PdfRenderConfig::new()
        .set_target_width(width)
        .set_maximum_height(max_height)
        .rotate_if_landscape(PdfPageRenderRotation::Degrees90, true);

    let mut paths = Vec::new();
    for (i, page) in document.pages().iter().enumerate() {
        let image = page
            .render_with_config(&render_config)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to render page {}: {}", i + 1, e)))?
            .as_image()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert page {} to image: {}", i + 1, e)))?;

        let output_path = output_dir.join(format!("page_{:04}.jpg", i + 1));

        let encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(
            std::fs::File::create(&output_path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to create file: {}", e)))?,
            quality,
        );
        image.write_with_encoder(encoder)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to save page {}: {}", i + 1, e)))?;

        paths.push(output_path.to_string_lossy().to_string());
    }

    Ok(paths)
}

/// Render a PDF file to JPEG images and return them as bytes.
///
/// Args:
///     pdf_path: Path to the input PDF file.
///     width: Target width in pixels (default: 2000).
///     max_height: Maximum height in pixels (default: 4000).
///     quality: JPEG quality 1-100 (default: 90).
///
/// Returns:
///     List of JPEG images as bytes objects.
#[pyfunction]
#[pyo3(signature = (pdf_path, width=2000, max_height=4000, quality=90))]
fn render_pdf_to_bytes(
    pdf_path: &str,
    width: i32,
    max_height: i32,
    quality: u8,
) -> PyResult<Vec<Vec<u8>>> {
    let pdfium = find_pdfium()
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to load pdfium library: {}", e)))?;

    let document = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to load PDF: {}", e)))?;

    let render_config = PdfRenderConfig::new()
        .set_target_width(width)
        .set_maximum_height(max_height)
        .rotate_if_landscape(PdfPageRenderRotation::Degrees90, true);

    let mut results = Vec::new();
    for (i, page) in document.pages().iter().enumerate() {
        let image = page
            .render_with_config(&render_config)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to render page {}: {}", i + 1, e)))?
            .as_image()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert page {}: {}", i + 1, e)))?;

        let mut buf = Cursor::new(Vec::new());
        let encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, quality);
        image.write_with_encoder(encoder)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to encode page {}: {}", i + 1, e)))?;

        results.push(buf.into_inner());
    }

    Ok(results)
}

/// Get the number of pages in a PDF file.
#[pyfunction]
fn page_count(pdf_path: &str) -> PyResult<usize> {
    let pdfium = find_pdfium()
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to load pdfium library: {}", e)))?;

    let document = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to load PDF: {}", e)))?;

    Ok(document.pages().len() as usize)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(convert_pdf_to_images, m)?)?;
    m.add_function(wrap_pyfunction!(render_pdf_to_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(page_count, m)?)?;
    Ok(())
}
