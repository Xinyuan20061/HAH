import { Camera, CameraResultType, CameraSource } from '@capacitor/camera'

export interface PickedPhoto {
  blob: Blob
  fileName: string
}

export async function captureOrChooseMealPhoto(): Promise<PickedPhoto> {
  const result = await Camera.getPhoto({
    quality: 78,
    width: 1600,
    height: 1600,
    resultType: CameraResultType.Uri,
    source: CameraSource.Prompt,
    correctOrientation: true,
  })
  const uri = result.webPath || (result.path ? `file://${result.path}` : '')
  if (!uri) throw new Error('无法读取这张照片，请重新选择')
  const response = await fetch(uri)
  if (!response.ok) throw new Error('无法读取这张照片，请重新选择')
  return {
    blob: await response.blob(),
    fileName: `餐食-${Date.now()}.${result.format || 'jpeg'}`,
  }
}
