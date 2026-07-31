/**
 * multipart/form-data 构造器。对齐 OkHttp MultipartBody.Part.createFormData("file", filename, body)。
 * manager 的文件上传端点（/files/upload、/auth/avatar）都要求 multipart 表单，
 * @ohos.net.http 没有封装，手工拼 boundary + 头部 + 二进制 + 尾界。
 */

export class MultipartBody {
  contentType: string = ''
  body: ArrayBuffer = new ArrayBuffer(0)
}

/** 纯算术 UTF-8 编码（不依赖 @ohos.buffer，其 Buffer.buffer/byteOffset 在运行时不稳）。 */
function encodeUtf8(s: string): Uint8Array {
  const out: number[] = []
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i)
    if (c < 0x80) {
      out.push(c)
    } else if (c < 0x800) {
      out.push(0xC0 | (c >> 6), 0x80 | (c & 0x3F))
    } else if (c >= 0xD800 && c <= 0xDBFF && i + 1 < s.length) {
      const c2 = s.charCodeAt(i + 1)
      if (c2 >= 0xDC00 && c2 <= 0xDFFF) {
        const cp = 0x10000 + ((c - 0xD800) << 10) + (c2 - 0xDC00)
        out.push(0xF0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3F), 0x80 | ((cp >> 6) & 0x3F), 0x80 | (cp & 0x3F))
        i++
      } else {
        out.push(0xEF, 0xBF, 0xBD)
      }
    } else {
      out.push(0xE0 | (c >> 12), 0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F))
    }
  }
  return new Uint8Array(out)
}

/** 构造单文件 multipart body（表单字段名固定 "file"）。 */
export function buildMultipartFileBody(file: Uint8Array, filename: string, mime: string): MultipartBody {
  const boundary = `----UA${Date.now().toString(16)}Boundary`
  const safeName = filename.replace(/["\r\n]/g, '_')
  const head = encodeUtf8(
    `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${safeName}"\r\nContent-Type: ${mime}\r\n\r\n`
  )
  const tail = encodeUtf8(`\r\n--${boundary}--\r\n`)
  const out = new Uint8Array(head.length + file.length + tail.length)
  out.set(head, 0)
  out.set(file, head.length)
  out.set(tail, head.length + file.length)
  const ab = new ArrayBuffer(out.length)
  new Uint8Array(ab).set(out)
  const result = new MultipartBody()
  result.contentType = `multipart/form-data; boundary=${boundary}`
  result.body = ab
  return result
}
