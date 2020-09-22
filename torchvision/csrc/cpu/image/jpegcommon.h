
#pragma once

#include <jpeglib.h>
#include <setjmp.h>
#include <string>

const static JOCTET EOI_BUFFER[1] = {JPEG_EOI};
const static size_t JPEG_BUF_SIZE = 16384;

struct torch_jpeg_error_mgr {
  struct jpeg_error_mgr pub; /* "public" fields */
  char jpegLastErrorMsg[JMSG_LENGTH_MAX]; /* error messages */
  jmp_buf setjmp_buffer; /* for return to caller */
};

typedef struct torch_jpeg_error_mgr* torch_jpeg_error_ptr;

void torch_jpeg_error_exit(j_common_ptr cinfo) {
  /* cinfo->err really points to a torch_jpeg_error_mgr struct, so coerce
   * pointer */
  torch_jpeg_error_ptr myerr = (torch_jpeg_error_ptr)cinfo->err;

  /* Always display the message. */
  /* We could postpone this until after returning, if we chose. */
  // (*cinfo->err->output_message)(cinfo);
  /* Create the message */
  (*(cinfo->err->format_message))(cinfo, myerr->jpegLastErrorMsg);

  /* Return control to the setjmp point */
  longjmp(myerr->setjmp_buffer, 1);
}
