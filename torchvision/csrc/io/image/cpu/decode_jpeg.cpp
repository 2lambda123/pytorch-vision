#include "decode_jpeg.h"
#include "common_jpeg.h"
#include "exif.h"

namespace vision {
namespace image {

#if !JPEG_FOUND
torch::Tensor decode_jpeg(const torch::Tensor& data, ImageReadMode mode) {
  TORCH_CHECK(
      false, "decode_jpeg: torchvision not compiled with libjpeg support");
}
#else

using namespace detail;
using namespace exif_private;

namespace {

struct torch_jpeg_mgr {
  struct jpeg_source_mgr pub;
  const JOCTET* data;
  size_t len;
};

static void torch_jpeg_init_source(j_decompress_ptr cinfo) {}

static boolean torch_jpeg_fill_input_buffer(j_decompress_ptr cinfo) {
  // No more data.  Probably an incomplete image;  Raise exception.
  torch_jpeg_error_ptr myerr = (torch_jpeg_error_ptr)cinfo->err;
  strcpy(myerr->jpegLastErrorMsg, "Image is incomplete or truncated");
  longjmp(myerr->setjmp_buffer, 1);
}

static void torch_jpeg_skip_input_data(j_decompress_ptr cinfo, long num_bytes) {
  torch_jpeg_mgr* src = (torch_jpeg_mgr*)cinfo->src;
  if (src->pub.bytes_in_buffer < (size_t)num_bytes) {
    // Skipping over all of remaining data;  output EOI.
    src->pub.next_input_byte = EOI_BUFFER;
    src->pub.bytes_in_buffer = 1;
  } else {
    // Skipping over only some of the remaining data.
    src->pub.next_input_byte += num_bytes;
    src->pub.bytes_in_buffer -= num_bytes;
  }
}

static void torch_jpeg_term_source(j_decompress_ptr cinfo) {}

static void torch_jpeg_set_source_mgr(
    j_decompress_ptr cinfo,
    const unsigned char* data,
    size_t len) {
  torch_jpeg_mgr* src;
  if (cinfo->src == 0) { // if this is first time;  allocate memory
    cinfo->src = (struct jpeg_source_mgr*)(*cinfo->mem->alloc_small)(
        (j_common_ptr)cinfo, JPOOL_PERMANENT, sizeof(torch_jpeg_mgr));
  }
  src = (torch_jpeg_mgr*)cinfo->src;
  src->pub.init_source = torch_jpeg_init_source;
  src->pub.fill_input_buffer = torch_jpeg_fill_input_buffer;
  src->pub.skip_input_data = torch_jpeg_skip_input_data;
  src->pub.resync_to_restart = jpeg_resync_to_restart; // default
  src->pub.term_source = torch_jpeg_term_source;
  // fill the buffers
  src->data = (const JOCTET*)data;
  src->len = len;
  src->pub.bytes_in_buffer = len;
  src->pub.next_input_byte = src->data;

  jpeg_save_markers(cinfo, APP1, 0xffff);
}

inline unsigned char clamped_cmyk_rgb_convert(
    unsigned char k,
    unsigned char cmy) {
  // Inspired from Pillow:
  // https://github.com/python-pillow/Pillow/blob/07623d1a7cc65206a5355fba2ae256550bfcaba6/src/libImaging/Convert.c#L568-L569
  int v = k * cmy + 128;
  v = ((v >> 8) + v) >> 8;
  return std::clamp(k - v, 0, 255);
}

void convert_line_cmyk_to_rgb(
    j_decompress_ptr cinfo,
    const unsigned char* cmyk_line,
    unsigned char* rgb_line) {
  int width = cinfo->output_width;
  for (int i = 0; i < width; ++i) {
    int c = cmyk_line[i * 4 + 0];
    int m = cmyk_line[i * 4 + 1];
    int y = cmyk_line[i * 4 + 2];
    int k = cmyk_line[i * 4 + 3];

    rgb_line[i * 3 + 0] = clamped_cmyk_rgb_convert(k, 255 - c);
    rgb_line[i * 3 + 1] = clamped_cmyk_rgb_convert(k, 255 - m);
    rgb_line[i * 3 + 2] = clamped_cmyk_rgb_convert(k, 255 - y);
  }
}

inline unsigned char rgb_to_gray(int r, int g, int b) {
  // Inspired from Pillow:
  // https://github.com/python-pillow/Pillow/blob/07623d1a7cc65206a5355fba2ae256550bfcaba6/src/libImaging/Convert.c#L226
  return (r * 19595 + g * 38470 + b * 7471 + 0x8000) >> 16;
}

void convert_line_cmyk_to_gray(
    j_decompress_ptr cinfo,
    const unsigned char* cmyk_line,
    unsigned char* gray_line) {
  int width = cinfo->output_width;
  for (int i = 0; i < width; ++i) {
    int c = cmyk_line[i * 4 + 0];
    int m = cmyk_line[i * 4 + 1];
    int y = cmyk_line[i * 4 + 2];
    int k = cmyk_line[i * 4 + 3];

    int r = clamped_cmyk_rgb_convert(k, 255 - c);
    int g = clamped_cmyk_rgb_convert(k, 255 - m);
    int b = clamped_cmyk_rgb_convert(k, 255 - y);

    gray_line[i] = rgb_to_gray(r, g, b);
  }
}

} // namespace

torch::Tensor decode_jpeg(
    const torch::Tensor& data,
    ImageReadMode mode,
    bool apply_exif_orientation) {
  C10_LOG_API_USAGE_ONCE(
      "torchvision.csrc.io.image.cpu.decode_jpeg.decode_jpeg");
  // Check that the input tensor dtype is uint8
  TORCH_CHECK(data.dtype() == torch::kU8, "Expected a torch.uint8 tensor");
  // Check that the input tensor is 1-dimensional
  TORCH_CHECK(
      data.dim() == 1 && data.numel() > 0,
      "Expected a non empty 1-dimensional tensor");

  struct jpeg_decompress_struct cinfo;
  struct torch_jpeg_error_mgr jerr;

  auto datap = data.data_ptr<uint8_t>();
  // Setup decompression structure
  cinfo.err = jpeg_std_error(&jerr.pub);
  jerr.pub.error_exit = torch_jpeg_error_exit;
  /* Establish the setjmp return context for my_error_exit to use. */
  if (setjmp(jerr.setjmp_buffer)) {
    /* If we get here, the JPEG code has signaled an error.
     * We need to clean up the JPEG object.
     */
    jpeg_destroy_decompress(&cinfo);
    TORCH_CHECK(false, jerr.jpegLastErrorMsg);
  }

  jpeg_create_decompress(&cinfo);
  torch_jpeg_set_source_mgr(&cinfo, datap, data.numel());

  // read info from header.
  jpeg_read_header(&cinfo, TRUE);

  int channels = cinfo.num_components;
  bool cmyk_to_rgb_or_gray = false;

  if (mode != IMAGE_READ_MODE_UNCHANGED) {
    switch (mode) {
      case IMAGE_READ_MODE_GRAY:
        if (cinfo.jpeg_color_space == JCS_CMYK ||
            cinfo.jpeg_color_space == JCS_YCCK) {
          cinfo.out_color_space = JCS_CMYK;
          cmyk_to_rgb_or_gray = true;
        } else {
          cinfo.out_color_space = JCS_GRAYSCALE;
        }
        channels = 1;
        break;
      case IMAGE_READ_MODE_RGB:
        if (cinfo.jpeg_color_space == JCS_CMYK ||
            cinfo.jpeg_color_space == JCS_YCCK) {
          cinfo.out_color_space = JCS_CMYK;
          cmyk_to_rgb_or_gray = true;
        } else {
          cinfo.out_color_space = JCS_RGB;
        }
        channels = 3;
        break;
      /*
       * Libjpeg does not support converting from CMYK to grayscale etc. There
       * is a way to do this but it involves converting it manually to RGB:
       * https://github.com/tensorflow/tensorflow/blob/86871065265b04e0db8ca360c046421efb2bdeb4/tensorflow/core/lib/jpeg/jpeg_mem.cc#L284-L313
       */
      default:
        jpeg_destroy_decompress(&cinfo);
        TORCH_CHECK(false, "The provided mode is not supported for JPEG files");
    }

    jpeg_calc_output_dimensions(&cinfo);
  }

  int exif_orientation = 0;
  if (apply_exif_orientation) {
    // Check for Exif marker APP1
    jpeg_saved_marker_ptr exif_marker = 0;
    jpeg_saved_marker_ptr cmarker = cinfo.marker_list;
    while (cmarker && exif_marker == 0) {
      if (cmarker->marker == APP1) {
        exif_marker = cmarker;
      }
      cmarker = cmarker->next;
    }

    if (exif_marker) {
      // Code below is inspired from OpenCV
      // https://github.com/opencv/opencv/blob/097891e311fae1d8354eb092a0fd0171e630d78c/modules/modules/imgcodecs/src/exif.cpp

      // Bytes from Exif size field to the first TIFF header
      constexpr size_t start_offset = 6;
      if (exif_marker->data_length > start_offset) {
        auto* exif_data_ptr = exif_marker->data + start_offset;
        auto size = exif_marker->data_length - start_offset;
        std::vector<unsigned char> exif_data_vec(
            exif_data_ptr, exif_data_ptr + size);

        auto endianness = get_endianness(exif_data_vec);

        // Checking whether Tag Mark (0x002A) correspond to one contained in the
        // Jpeg file
        uint16_t tag_mark = get_uint16(exif_data_vec, endianness, 2);
        if (tag_mark == REQ_EXIF_TAG_MARK) {
          auto offset = get_uint32(exif_data_vec, endianness, 4);
          size_t num_entry = get_uint16(exif_data_vec, endianness, offset);
          offset += 2; // go to start of tag fields
          constexpr size_t tiff_field_size = 12;
          for (size_t entry = 0; entry < num_entry; entry++) {
            // Here we just search for orientation tag and parse it
            auto tag_num = get_uint16(exif_data_vec, endianness, offset);
            if (tag_num == ORIENTATION_EXIF_TAG) {
              exif_orientation =
                  get_uint16(exif_data_vec, endianness, offset + 8);
            }
            offset += tiff_field_size;
          }
        }
      }
    }
  }

  jpeg_start_decompress(&cinfo);

  int height = cinfo.output_height;
  int width = cinfo.output_width;

  int stride = width * channels;
  auto tensor =
      torch::empty({int64_t(height), int64_t(width), channels}, torch::kU8);
  auto ptr = tensor.data_ptr<uint8_t>();
  torch::Tensor cmyk_line_tensor;
  if (cmyk_to_rgb_or_gray) {
    cmyk_line_tensor = torch::empty({int64_t(width), 4}, torch::kU8);
  }

  while (cinfo.output_scanline < cinfo.output_height) {
    /* jpeg_read_scanlines expects an array of pointers to scanlines.
     * Here the array is only one element long, but you could ask for
     * more than one scanline at a time if that's more convenient.
     */
    if (cmyk_to_rgb_or_gray) {
      auto cmyk_line_ptr = cmyk_line_tensor.data_ptr<uint8_t>();
      jpeg_read_scanlines(&cinfo, &cmyk_line_ptr, 1);

      if (channels == 3) {
        convert_line_cmyk_to_rgb(&cinfo, cmyk_line_ptr, ptr);
      } else if (channels == 1) {
        convert_line_cmyk_to_gray(&cinfo, cmyk_line_ptr, ptr);
      }
    } else {
      jpeg_read_scanlines(&cinfo, &ptr, 1);
    }
    ptr += stride;
  }

  jpeg_finish_decompress(&cinfo);
  jpeg_destroy_decompress(&cinfo);
  auto output = tensor.permute({2, 0, 1});

  if (apply_exif_orientation) {
    return exif_orientation_transform(output, exif_orientation);
  }
  return output;
}
#endif // #if !JPEG_FOUND

int64_t _jpeg_version() {
#if JPEG_FOUND
  return JPEG_LIB_VERSION;
#else
  return -1;
#endif
}

bool _is_compiled_against_turbo() {
#ifdef LIBJPEG_TURBO_VERSION
  return true;
#else
  return false;
#endif
}

} // namespace image
} // namespace vision
