import { ManagerApi, ApiError } from '../net/ManagerApi';
import { WorkspaceFileList, WorkspaceFileContent, WorkspaceFileEntry, CreateFolderRequest, MoveFileRequest } from '../net/dto/WorkspaceDtos';
import { UploadFileResponse } from '../net/dto/ChatDtos';

/**
 * 工作区仓库。复刻 Android repo/WorkspaceRepository.kt。
 * 文件列表 / 读 / 上传 / 创建目录 / 删除 / 移动。
 * 上传/下载字节流走 @ohos.file.fs（Phase 1 简化：用 Uint8Array）。
 */
export class WorkspaceRepository {
  private managerApi: ManagerApi

  constructor(managerApi: ManagerApi) {
    this.managerApi = managerApi
  }

  async listFiles(agentId: string, path: string): Promise<WorkspaceFileList> {
    const resp = await this.managerApi.listFiles(agentId, path)
    // 目录在前，再按名排序
    const sorted = resp.entries.slice().sort((a: WorkspaceFileEntry, b: WorkspaceFileEntry) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
      return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0)
    })
    const out = new WorkspaceFileList()
    out.entries = sorted
    out.path = resp.path
    out.error = resp.error
    return out
  }

  async readFile(agentId: string, path: string): Promise<WorkspaceFileContent> {
    return await this.managerApi.readFile(agentId, path)
  }

  /**
   * 上传文件到工作区指定目录。
   * Phase 1 简化：bytes + filename + mime 直接走 multipart 等价二进制。
   */
  async uploadFile(agentId: string, dir: string, file: Uint8Array, filename: string, mime: string): Promise<UploadFileResponse> {
    return await this.managerApi.uploadAgentFile(agentId, dir, file, filename, mime)
  }

  async createFolder(agentId: string, parentPath: string, name: string): Promise<void> {
    const req = new CreateFolderRequest()
    req.name = name
    await this.managerApi.createFolder(agentId, parentPath, req)
  }

  async deleteFile(agentId: string, path: string): Promise<void> {
    await this.managerApi.deleteFile(agentId, path)
  }

  async moveFile(agentId: string, fromPath: string, toPath: string): Promise<void> {
    const req = new MoveFileRequest()
    req.from_path = fromPath
    req.to_path = toPath
    await this.managerApi.moveFile(agentId, req)
  }

  /**
   * 下载二进制文件。返回 ArrayBuffer（PDF/图片/Office 等）。
   * 失败返回 null（调用方静默处理）。
   */
  async downloadFile(agentId: string, path: string): Promise<ArrayBuffer | null> {
    try {
      return await this.managerApi.downloadFile(agentId, path)
    } catch (e) {
      return null
    }
  }
}

/** 目录在前的排序工具。 */
export function dirsFirst(entries: WorkspaceFileEntry[]): WorkspaceFileEntry[] {
  return entries.slice().sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0)
  })
}
