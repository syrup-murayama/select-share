return {
  LrSdkVersion = 6.0,
  LrSdkMinimumVersion = 6.0,
  LrToolkitIdentifier = 'com.muraya.select-share-runner',
  LrPluginName = 'Select Share ビルダー',
  LrPluginInfoUrl = 'https://github.com/syrup-murayama/photo-workflow',
  VERSION = { major = 1, minor = 0, revision = 0, build = 0 },
  LrLibraryMenuItems = {
    {
      title = 'Select Share ビルダー...',
      file = 'RunDialog.lua',
    },
    {
      title = 'セレクト結果を読み込む…',
      file = 'ImportMenuItem.lua',
    },
  },
}