<template>
  <div class="studio-page">
    <aside class="library-sidebar" aria-label="提示词库导航">
      <div class="brand-lockup">
        <span class="brand-mark">小</span>
        <span class="brand-copy"><strong>小恐龙</strong><small>图像创作工作台</small></span>
        <a
          class="github-link"
          href="https://github.com/yukkcat/klong-skills"
          target="_blank"
          rel="noreferrer"
          title="在 GitHub 查看 klong-skills"
          aria-label="在 GitHub 查看 klong-skills"
        >
          <Icon icon="lucide:github" />
        </a>
      </div>

      <nav class="workspace-tabs" aria-label="工作区">
        <button type="button" :class="{ active: activeView === 'prompts' }" @click="switchView('prompts')">
          <Icon icon="lucide:library-big" />
          <span>提示词</span>
        </button>
        <button type="button" :class="{ active: activeView === 'create' }" @click="startNewCreation">
          <Icon icon="lucide:wand-sparkles" />
          <span>创作</span>
        </button>
        <button type="button" :class="{ active: activeView === 'gallery' }" @click="switchView('gallery')">
          <Icon icon="lucide:images" />
          <span>图库</span>
        </button>
      </nav>

      <nav v-if="activeView === 'prompts'" class="sidebar-navigation">
        <section class="nav-section browse-section">
          <button class="nav-home" :class="{ active: !hasFilters }" type="button" @click="clearFilters">
            <Icon icon="lucide:layout-grid" />
            <span class="nav-home-copy"><strong>全部提示词</strong></span>
            <small class="nav-home-count">{{ library.prompt_count.toLocaleString() }}</small>
          </button>
        </section>

        <section v-if="library.sources.length" class="nav-section source-section">
          <p class="nav-label"><span>词源</span><small>{{ library.sources.length }}</small></p>
          <button
            v-for="source in library.sources"
            :key="source.id"
            class="nav-item source-item"
            :class="{ active: filters.source === source.id }"
            type="button"
            :title="source.name"
            @click="browseSource(source.id)"
          >
            <span class="source-badge" :class="`source-badge-${source.id}`">{{ sourceBadge(source.id) }}</span>
            <span class="item-label">{{ sourceShortName(source) }}</span>
            <small>{{ Number(source.count || 0).toLocaleString() }}</small>
          </button>
        </section>

        <section v-if="categories.length" class="nav-section category-section">
          <p class="nav-label"><span>分类</span><small>{{ categories.length }}</small></p>
          <div class="category-scroll">
            <button
              v-for="category in categories"
              :key="category"
              class="nav-item category-item"
              :class="{ active: filters.category === category }"
              type="button"
              :title="category"
              @click="browseCategory(category)"
            >
              <span class="category-marker"></span>
              <span class="item-label">{{ category }}</span>
            </button>
          </div>
        </section>
      </nav>

      <nav v-else-if="activeView === 'gallery'" class="sidebar-navigation gallery-navigation">
        <section class="nav-section browse-section">
          <button class="nav-home active" type="button" @click="clearGallerySearch">
            <Icon icon="lucide:gallery-horizontal-end" />
            <span class="nav-home-copy"><strong>全部作品</strong></span>
            <small class="nav-home-count">{{ galleryTotal.toLocaleString() }}</small>
          </button>
        </section>
      </nav>

      <nav v-else class="sidebar-navigation creation-navigation">
        <section class="nav-section browse-section">
          <button
            class="nav-home"
            :class="{ active: !job }"
            type="button"
            :aria-current="!job ? 'page' : undefined"
            @click="startNewCreation"
          >
            <Icon icon="lucide:file-plus-2" />
            <span class="nav-home-copy"><strong>新建创作</strong></span>
          </button>
        </section>
        <section class="creation-history-section">
          <div class="creation-history-heading">
            <span><Icon icon="lucide:history" />最近生成</span>
            <small>{{ historyTotal }}</small>
          </div>
          <div v-if="jobHistory.length" class="creation-history-list">
            <button
              v-for="item in jobHistory"
              :key="item.id"
              class="creation-history-item"
              :class="[{ active: job?.id === item.id }, `status-${item.status}`]"
              type="button"
              :title="historyItemTitle(item)"
              :aria-current="job?.id === item.id ? 'true' : undefined"
              @click="restoreHistoryJob(item)"
            >
              <span class="creation-history-media">
                <img v-if="item.thumbnail_url" :src="item.thumbnail_url" alt="" />
                <Icon v-else :icon="item.status === 'failed' ? 'lucide:image-off' : 'lucide:image'" />
                <span class="creation-history-status"><Icon :icon="historyStatusIcon(item.status)" /></span>
              </span>
              <span class="creation-history-copy">
                <strong>{{ historyItemTitle(item) }}</strong>
                <small><span>{{ item.model }}</span><span>{{ item.count }} 张</span><time>{{ formatDate(item.created_at) }}</time></small>
              </span>
            </button>
          </div>
          <div v-else class="creation-history-empty">
            <Icon icon="lucide:history" />
            <span>生成记录会保留在这里</span>
          </div>
        </section>
      </nav>

      <div v-if="activeView === 'prompts'" class="sidebar-status">
        <span class="sync-dot" :class="{ busy: library.syncing }"></span>
        <span><strong>{{ syncLabel }}</strong><small>{{ readySources }} 个词源可用</small></span>
        <Button
          size="sm"
          variant="outline"
          root-class="sync-button"
          title="更新提示词库"
          :disabled="library.syncing"
          @click="refreshAll"
        >
          <Icon icon="lucide:refresh-cw" :class="{ spin: library.syncing }" />
          {{ library.syncing ? '同步中' : '同步' }}
        </Button>
      </div>
      <div v-else class="sidebar-status connection-status">
        <span class="sync-dot" :class="{ disconnected: !activeConnection?.key_configured }"></span>
        <span><strong>{{ connectionLabel }}</strong><small>{{ activeConnection?.default_model || '尚未选择模型' }}</small></span>
        <Button size="sm" variant="outline" root-class="sync-button" @click="openConnection">
          <Icon icon="lucide:settings-2" />
          设置
        </Button>
      </div>
    </aside>

    <section class="library-workspace">
      <header class="workspace-bar">
        <div class="workspace-context">
          <span>{{ workspaceParentLabel }}</span>
          <Icon icon="lucide:chevron-right" />
          <strong>{{ workspaceCurrentLabel }}</strong>
        </div>
        <div class="mobile-wordmark"><span class="brand-mark">小</span><strong>{{ mobileViewLabel }}</strong></div>
        <div class="mobile-view-switch" aria-label="切换工作区">
          <button type="button" :class="{ active: activeView === 'prompts' }" title="提示词" @click="switchView('prompts')"><Icon icon="lucide:library-big" /></button>
          <button type="button" :class="{ active: activeView === 'create' }" title="新建创作" @click="startNewCreation"><Icon icon="lucide:wand-sparkles" /></button>
          <button type="button" :class="{ active: activeView === 'gallery' }" title="图库" @click="switchView('gallery')"><Icon icon="lucide:images" /></button>
        </div>

        <div v-if="activeView !== 'create'" class="search-field">
          <Icon icon="lucide:search" />
          <Input
            v-model="activeSearch"
            type="search"
            size="md"
            block
            root-class="prompt-search"
            :placeholder="activeView === 'prompts' ? '搜索提示词' : '搜索作品、模型或提示词'"
          />
          <Button
            v-if="activeSearch"
            size="xs"
            variant="ghost"
            icon-only
            root-class="clear-search"
            title="清除搜索"
            @click="clearActiveSearch"
          >
            <Icon icon="lucide:x" />
          </Button>
        </div>

        <Button
          v-if="activeView === 'prompts'"
          size="md"
          variant="outline"
          icon-only
          root-class="mobile-refresh"
          title="更新提示词库"
          :disabled="library.syncing"
          @click="refreshAll"
        >
          <Icon icon="lucide:refresh-cw" :class="{ spin: library.syncing }" />
        </Button>
        <Button
          v-else-if="activeView === 'gallery'"
          size="md"
          variant="outline"
          icon-only
          root-class="gallery-refresh"
          title="刷新图库"
          :disabled="loadingGallery"
           @click="resetGallery()"
        >
          <Icon icon="lucide:refresh-cw" :class="{ spin: loadingGallery }" />
        </Button>
        <Button
          size="md"
          variant="outline"
          icon-only
          root-class="theme-toggle"
          :title="colorTheme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'"
          :aria-label="colorTheme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'"
          :aria-pressed="colorTheme === 'dark'"
          @click="toggleColorTheme"
        >
          <Icon :icon="colorTheme === 'dark' ? 'lucide:sun' : 'lucide:moon'" />
        </Button>
        <Button
          size="md"
          variant="outline"
          root-class="connection-button"
          :title="connectionLabel"
          @click="openConnection"
        >
          <span class="connection-dot" :class="{ ready: activeConnection?.key_configured }"></span>
          <Icon icon="lucide:settings-2" />
          <span class="connection-button-label">{{ connectionLabel }}</span>
        </Button>
      </header>

      <main
        class="library-content"
        :class="{
          'gallery-content': activeView === 'gallery',
          'gallery-has-selection': activeView === 'gallery' && gallerySelectionCount,
          'creation-content': activeView === 'create'
        }"
      >
        <template v-if="activeView === 'prompts'">
        <section class="mobile-filters" aria-label="筛选提示词">
          <div class="filter-control">
            <FilterSelect
              v-model="filters.source"
              :options="sourceOptions"
              size="md"
              placement="down"
              selected-indicator="check"
              aria-label="提示词来源"
            />
          </div>
          <div class="filter-control">
            <FilterSelect
              v-model="filters.category"
              :options="categoryOptions"
              size="md"
              placement="down"
              selected-indicator="check"
              aria-label="提示词分类"
            />
          </div>
        </section>

        <section v-if="loadingPrompts && prompts.length === 0" class="prompt-grid" aria-label="正在加载提示词">
          <article v-for="index in 12" :key="index" class="prompt-tile skeleton-card">
            <div class="prompt-media skeleton-block"></div>
            <div class="prompt-caption"><i class="skeleton-line"></i><i class="skeleton-line short"></i></div>
          </article>
        </section>

        <EmptyState
          v-else-if="loadError && prompts.length === 0"
          title="提示词加载失败"
          :description="loadError"
          variant="outline"
          root-class="library-empty"
        >
          <template #icon><Icon icon="lucide:triangle-alert" /></template>
          <template #actions><Button size="sm" variant="outline" @click="resetPrompts">重新加载</Button></template>
        </EmptyState>

        <EmptyState
          v-else-if="prompts.length === 0"
          title="没有匹配的提示词"
          description="换一个关键词、来源或分类再试。"
          variant="outline"
          root-class="library-empty"
        >
          <template #icon><Icon icon="lucide:search-x" /></template>
          <template #actions><Button size="sm" variant="outline" @click="clearFilters">清除筛选</Button></template>
        </EmptyState>

        <section v-else class="prompt-grid" aria-live="polite">
          <article
            v-for="item in prompts"
            :key="item.id"
            class="prompt-tile"
            role="button"
            tabindex="0"
            @click="choosePrompt(item)"
            @keydown.enter="choosePrompt(item)"
            @keydown.space.prevent="choosePrompt(item)"
          >
            <div class="prompt-media" :class="{ empty: !preview(item) }">
              <img
                v-if="preview(item)"
                :src="preview(item)"
                :alt="item.title"
                loading="lazy"
                decoding="async"
                @error="onPreviewError(item)"
              />
              <div v-else class="missing-preview"><Icon icon="lucide:image-off" /><span>暂无预览</span></div>
              <span v-if="categoryLabel(item)" class="media-tag">{{ categoryLabel(item) }}</span>
              <span class="tile-action" aria-hidden="true"><Icon icon="lucide:arrow-up-right" /></span>
            </div>
            <div class="prompt-caption">
              <h2>{{ item.title }}</h2>
              <p><span>{{ item.source_name || '本地词库' }}</span><span>{{ promptLength(item.prompt) }}</span></p>
            </div>
          </article>
        </section>

        <div ref="loadSentinel" class="load-sentinel" aria-live="polite">
          <span v-if="loadingPrompts && prompts.length"><Icon icon="lucide:loader-circle" class="spin" />正在载入更多</span>
          <Button v-else-if="loadError && prompts.length" size="sm" variant="outline" @click="loadNextPage">继续加载</Button>
          <span v-else-if="hasMore">继续向下浏览</span>
          <span v-else-if="prompts.length"><Icon icon="lucide:check" />已显示全部 {{ promptTotal.toLocaleString() }} 条</span>
        </div>
        </template>

        <template v-else-if="activeView === 'create'">
          <section class="creation-layout">
            <aside class="creation-controls" aria-label="创作设置">
              <header class="creation-controls-header">
                <div><span class="eyebrow">CREATE</span><h1>创作设置</h1></div>
                <div class="creation-header-actions">
                  <Button
                    v-if="job || selected || form.prompt.trim() || inputFile"
                    size="sm"
                    variant="outline"
                    @click="startNewCreation"
                  >
                    <Icon icon="lucide:plus" />
                    新建
                  </Button>
                  <Button size="sm" variant="ghost" @click="switchView('prompts')">
                    <Icon icon="lucide:library-big" />
                    提示词库
                  </Button>
                </div>
              </header>

              <div class="creation-controls-scroll">
                <section v-if="selected" class="creation-prompt-summary">
                  <div class="creation-prompt-thumb" :class="{ empty: !preview(selected) }">
                    <img v-if="preview(selected)" :src="preview(selected)" :alt="selected.title" @error="onPreviewError(selected)" />
                    <Icon v-else icon="lucide:image-off" />
                  </div>
                  <div>
                    <span>{{ selected.source_name || '提示词库' }}</span>
                    <strong>{{ selected.title }}</strong>
                    <small>{{ categoryLabel(selected) || '未分类' }}</small>
                  </div>
                </section>

                <section class="creation-control-section">
                  <FormField label="提示词" :hint="`${Array.from(form.prompt).length.toLocaleString()} 字`">
                    <textarea v-model="form.prompt" class="workbench-prompt-textarea" placeholder="描述你想生成的画面"></textarea>
                  </FormField>
                </section>

                <section class="creation-control-section reference-section">
                  <div class="section-title"><h3>参考图片</h3><span>{{ inputFile ? '图生图' : '可选' }}</span></div>
                  <label class="workbench-upload" :class="{ active: inputFile }">
                    <input ref="inputFileControl" type="file" accept="image/png,image/jpeg,image/webp" @change="onFile" />
                    <span v-if="inputPreviewUrl" class="reference-thumbnail"><img :src="inputPreviewUrl" :alt="inputFile?.name || '参考图片'" /></span>
                    <span v-else class="reference-placeholder"><Icon icon="lucide:image-plus" /></span>
                    <span class="reference-copy">
                      <strong>{{ inputFile ? inputFile.name : '添加参考图片' }}</strong>
                      <small>{{ inputFile ? formatBytes(inputFile.size) : 'PNG、JPEG 或 WebP，最大 20 MiB' }}</small>
                    </span>
                    <Button v-if="inputFile" size="xs" variant="ghost" icon-only title="移除参考图片" @click.prevent.stop="clearInputFile">
                      <Icon icon="lucide:x" />
                    </Button>
                  </label>
                </section>

                <section class="creation-control-section parameter-section">
                  <div class="section-title"><h3>生成参数</h3><span>{{ serialModel ? '当前模型仅支持串行' : '按需调整' }}</span></div>
                  <FormField label="连接">
                    <FilterSelect
                      v-model="form.connection_id"
                      class="workbench-model-select"
                      :options="connectionOptions"
                      size="md"
                      placement="down"
                      selected-indicator="check"
                      aria-label="生成连接"
                      @update:model-value="changeWorkbenchConnection"
                    />
                  </FormField>
                  <FormField label="模型">
                    <FilterSelect
                      v-model="form.model"
                      class="workbench-model-select"
                      :options="modelOptions"
                      size="md"
                      placement="down"
                      selected-indicator="check"
                      aria-label="生成模型"
                      @update:model-value="normalizeModel"
                    />
                  </FormField>
                  <div class="field-row">
                    <FormField label="尺寸"><Input v-model="form.size" size="md" block :disabled="isGemini" placeholder="1024x1024" /></FormField>
                    <FormField label="数量"><Input v-model="form.count" type="number" min="1" size="md" block /></FormField>
                  </div>
                  <div class="field-row compact-row">
                    <FormField label="并发"><Input v-model="form.concurrency" type="number" min="1" size="md" block :disabled="serialModel" /></FormField>
                    <FormField label="输出格式"><Input model-value="PNG" size="md" block disabled /></FormField>
                  </div>
                  <details class="advanced-settings">
                    <summary><span><Icon icon="lucide:sliders-horizontal" />高级设置</span><Icon icon="lucide:chevron-down" /></summary>
                    <div><FormField label="文件名"><Input v-model="form.filename" size="md" block /></FormField></div>
                  </details>
                </section>
              </div>

              <footer class="creation-actionbar">
                <button type="button" class="creation-connection" @click="openConnection">
                  <span class="connection-dot" :class="{ ready: generationConnection?.key_configured }"></span>
                  <span><strong>{{ generationConnection?.name || '配置连接' }}</strong><small>{{ connectionStateLabel }}</small></span>
                  <Icon icon="lucide:chevron-right" />
                </button>
                <Button
                  size="md"
                  variant="primary"
                  root-class="workbench-generate-button"
                  :disabled="submitting || !form.prompt.trim()"
                  @click="createJob"
                >
                  <Icon :icon="submitting ? 'lucide:loader-circle' : 'lucide:wand-sparkles'" :class="{ spin: submitting }" />
                  {{ submitting ? '正在生成' : `生成 ${Math.max(1, Number(form.count) || 1)} 张` }}
                </Button>
              </footer>
            </aside>

            <section class="creation-stage" aria-label="预览与生成结果">
              <header class="creation-stage-header">
                <div><span class="eyebrow">OUTPUT</span><h2>{{ job?.result?.images?.length ? '生成结果' : '预览' }}</h2></div>
                <Button v-if="job?.result?.images?.length" size="sm" variant="outline" @click="showGalleryFromJob">
                  <Icon icon="lucide:images" />
                  打开图库
                </Button>
              </header>

              <section v-if="job" class="creation-job-strip">
                <div class="job-title">
                  <div><h3>{{ statusLabel(job.status) }}</h3><p>{{ job.model }} · {{ job.count }} 张 · 并发 {{ job.concurrency }}</p></div>
                  <MetaChip :tone="jobTone" variant="soft" size="sm">{{ jobPercent }}%</MetaChip>
                </div>
                <div class="job-progress"><div :style="{ width: `${jobPercent}%` }"></div></div>
                <div class="job-stats">
                  <span><strong>{{ job.result?.succeeded || 0 }}</strong>成功</span>
                  <span><strong>{{ job.result?.failed || 0 }}</strong>失败</span>
                  <span><strong>{{ job.result?.duration_seconds || elapsed }}</strong>秒</span>
                </div>
                <p v-if="job.error" class="job-error">{{ job.error }}</p>
              </section>

              <section v-if="job?.result?.images?.length" class="creation-results-grid">
                <button v-for="image in job.result.images" :key="image.index" type="button" @click="openGeneratedImage(image)">
                  <span class="creation-result-media">
                    <img v-if="image.url" :src="image.url" :alt="`生成结果 ${image.index}`" />
                    <Icon v-else icon="lucide:image" />
                    <span><Icon icon="lucide:maximize-2" /></span>
                  </span>
                  <span class="creation-result-caption">
                    <strong>结果 {{ image.index }}</strong>
                    <small>{{ image.width }} × {{ image.height }} · {{ formatBytes(image.bytes) }} · {{ image.duration_seconds }} 秒</small>
                  </span>
                </button>
              </section>

              <section v-else-if="selected && !job" class="creation-preview">
                <div class="creation-preview-media" :class="{ empty: !preview(selected) }">
                  <img v-if="preview(selected)" :src="preview(selected)" :alt="selected.title" @error="onPreviewError(selected)" />
                  <div v-else class="missing-preview"><Icon icon="lucide:image-off" /><span>暂无参考预览</span></div>
                </div>
                <div class="creation-preview-caption">
                  <span>{{ selected.source_name || '提示词库' }}</span>
                  <h2>{{ selected.title }}</h2>
                  <p>{{ categoryLabel(selected) || '未分类' }} · {{ promptLength(form.prompt) }}</p>
                </div>
              </section>

              <EmptyState
                v-else-if="!job"
                :title="form.prompt.trim() ? '准备生成' : '开始一个新创作'"
                :description="form.prompt.trim() ? '确认左侧参数后即可开始生成。' : '输入提示词，或从提示词库载入一个灵感。'"
                variant="outline"
                root-class="creation-empty"
              >
                <template #icon><Icon icon="lucide:wand-sparkles" /></template>
                <template #actions><Button size="sm" variant="outline" @click="switchView('prompts')">浏览提示词</Button></template>
              </EmptyState>

              <details v-if="job?.progress?.length" class="creation-log">
                <summary>运行日志</summary>
                <pre>{{ job.progress.slice(-12).join('\n') }}</pre>
              </details>
            </section>
          </section>
        </template>

        <template v-else>
          <section class="gallery-toolbar" aria-label="图库工具栏">
            <label v-if="galleryItems.length" class="gallery-page-select">
              <input type="checkbox" :checked="galleryPageSelected" @change="toggleGalleryPage" />
              <span>本页</span>
            </label>
            <span class="gallery-toolbar-total">{{ galleryTotal.toLocaleString() }} 张</span>
            <div class="gallery-toolbar-spacer"></div>
            <div class="gallery-sort-control">
              <FilterSelect
                v-model="gallerySort"
                :options="gallerySortOptions"
                size="sm"
                placement="down"
                selected-indicator="check"
                aria-label="图库排序"
              />
            </div>
            <div class="gallery-size-control">
              <FilterSelect
                v-model="galleryPageSize"
                :options="galleryPageSizeOptions"
                size="sm"
                placement="down"
                selected-indicator="check"
                aria-label="每页显示数量"
              />
            </div>
          </section>

          <section v-if="gallerySelectionCount" class="gallery-selection-bar" aria-live="polite">
            <span><Icon icon="lucide:check-square-2" />已选 {{ gallerySelectionCount.toLocaleString() }} 张</span>
            <button
              v-if="!galleryAllResultsSelected && gallerySelectionCount < galleryTotal"
              type="button"
              class="selection-link"
              @click="selectAllGalleryResults"
            >
              选择全部 {{ galleryTotal.toLocaleString() }} 张结果
            </button>
            <span v-else-if="galleryAllResultsSelected" class="selection-all">已选择当前筛选的全部结果</span>
            <div class="gallery-selection-actions">
              <Button size="sm" variant="outline" :disabled="galleryActionBusy" @click="downloadGalleryArchive">
                <Icon :icon="galleryArchiveBusy ? 'lucide:loader-circle' : 'lucide:file-archive'" :class="{ spin: galleryArchiveBusy }" />
                打包下载
              </Button>
              <Button
                size="sm"
                variant="outline"
                root-class="danger-button"
                :disabled="galleryActionBusy"
                @click="runGalleryAction('delete')"
              >
                <Icon icon="lucide:trash-2" />删除
              </Button>
              <Button size="sm" variant="ghost" icon-only title="取消选择" :disabled="galleryActionBusy" @click="clearGallerySelection">
                <Icon icon="lucide:x" />
              </Button>
            </div>
          </section>

          <section v-if="loadingGallery && galleryItems.length === 0" class="gallery-grid" aria-label="正在加载图库">
            <article v-for="index in 12" :key="index" class="gallery-tile skeleton-card">
              <div class="gallery-media skeleton-block"></div>
              <div class="gallery-caption"><i class="skeleton-line"></i><i class="skeleton-line short"></i></div>
            </article>
          </section>

          <EmptyState
            v-else-if="galleryError && galleryItems.length === 0"
            title="图库加载失败"
            :description="galleryError"
            variant="outline"
            root-class="library-empty"
          >
            <template #icon><Icon icon="lucide:triangle-alert" /></template>
            <template #actions><Button size="sm" variant="outline" @click="resetGallery()">重新加载</Button></template>
          </EmptyState>

          <EmptyState
            v-else-if="galleryItems.length === 0"
            :title="galleryKeyword ? '没有匹配的作品' : '图库还是空的'"
            :description="galleryKeyword ? '换一个关键词再试。' : '从提示词库选择一个提示词开始生成。'"
            variant="outline"
            root-class="library-empty gallery-empty"
          >
            <template #icon><Icon icon="lucide:images" /></template>
            <template #actions>
              <Button size="sm" variant="outline" @click="galleryKeyword ? clearGallerySearch() : switchView('prompts')">
                {{ galleryKeyword ? '清除搜索' : '浏览提示词' }}
              </Button>
            </template>
          </EmptyState>

          <section v-else class="gallery-grid" aria-live="polite">
            <article
              v-for="image in galleryItems"
              :key="image.id"
              class="gallery-tile"
              :class="{ selected: isGallerySelected(image.id) }"
            >
              <button type="button" class="gallery-open" @click="openGalleryImage(image)">
                <span class="gallery-media">
                  <img :src="image.url" :alt="image.name" loading="lazy" decoding="async" />
                  <span v-if="image.model" class="media-tag">{{ image.model }}</span>
                  <span class="tile-action" aria-hidden="true"><Icon icon="lucide:maximize-2" /></span>
                </span>
                <span class="gallery-caption">
                  <span class="gallery-name">{{ image.name }}</span>
                  <span class="gallery-facts"><span>{{ imageDimensions(image) }}</span><span>{{ formatDate(image.created_at) }}</span></span>
                </span>
              </button>
              <button
                type="button"
                class="gallery-tile-select"
                :class="{ active: isGallerySelected(image.id) }"
                :title="isGallerySelected(image.id) ? '取消选择' : '选择作品'"
                @click="toggleGalleryItem(image.id)"
              >
                <Icon :icon="isGallerySelected(image.id) ? 'lucide:check' : 'lucide:square'" />
              </button>
            </article>
          </section>

          <nav v-if="galleryPageCount > 1" class="gallery-pagination" aria-label="图库分页">
            <span>第 {{ galleryPage }} / {{ galleryPageCount }} 页</span>
            <div>
              <Button size="sm" variant="outline" icon-only title="上一页" :disabled="galleryPage <= 1 || loadingGallery" @click="goGalleryPage(galleryPage - 1)">
                <Icon icon="lucide:chevron-left" />
              </Button>
              <template v-for="page in galleryPageNumbers" :key="page">
                <span v-if="typeof page !== 'number'" class="pagination-gap">…</span>
                <button v-else type="button" class="pagination-page" :class="{ active: page === galleryPage }" :disabled="loadingGallery" @click="goGalleryPage(page)">{{ page }}</button>
              </template>
              <Button size="sm" variant="outline" icon-only title="下一页" :disabled="galleryPage >= galleryPageCount || loadingGallery" @click="goGalleryPage(galleryPage + 1)">
                <Icon icon="lucide:chevron-right" />
              </Button>
            </div>
          </nav>
        </template>
      </main>
    </section>

    <div v-if="settingsOpen" class="drawer-layer" role="presentation" @mousedown.self="closeSettings">
      <aside class="prompt-drawer" role="dialog" aria-modal="true" :aria-label="selected?.title || '提示词详情'">
        <header class="detail-drawer-header">
          <div
            v-if="selected"
            class="detail-drawer-preview"
            :class="{ empty: !preview(selected), clickable: !!preview(selected) }"
            :role="preview(selected) ? 'button' : undefined"
            :tabindex="preview(selected) ? 0 : undefined"
            :title="preview(selected) ? '点击查看大图' : undefined"
            @click="openPromptPreview"
            @keydown.enter="openPromptPreview"
            @keydown.space.prevent="openPromptPreview"
          >
            <img v-if="preview(selected)" :src="preview(selected)" :alt="selected.title" @error="onPreviewError(selected)" />
            <span v-if="preview(selected)" class="detail-preview-expand" aria-hidden="true"><Icon icon="lucide:maximize-2" /></span>
            <div v-else class="missing-preview"><Icon icon="lucide:image-off" /><span>暂无预览</span></div>
          </div>
          <div class="detail-drawer-heading">
            <span class="eyebrow">{{ selected?.source_name || '提示词详情' }}</span>
            <h2>{{ selected?.title || '提示词详情' }}</h2>
            <div class="detail-drawer-meta">
              <span v-if="selected && categoryLabel(selected)"><Icon icon="lucide:tag" />{{ categoryLabel(selected) }}</span>
              <span><Icon icon="lucide:align-left" />{{ promptLength(selected?.prompt || '') }}</span>
            </div>
          </div>
          <Button size="md" variant="ghost" icon-only root-class="drawer-close" title="关闭" @click="closeSettings">
            <Icon icon="lucide:x" />
          </Button>
        </header>

        <div class="detail-drawer-scroll">
          <section class="detail-prompt-section">
            <div class="section-title"><h3>提示词</h3><Button size="sm" variant="ghost" @click="copyPrompt(selected?.prompt || '')"><Icon icon="lucide:copy" />复制</Button></div>
            <div class="detail-prompt-copy">{{ selected?.prompt || '' }}</div>
          </section>
          <section v-if="selected?.description && selected.description !== selected.prompt" class="detail-description-section">
            <div class="section-title"><h3>说明</h3></div>
            <p>{{ selected.description }}</p>
          </section>
        </div>

        <footer class="detail-drawer-footer">
          <Button size="md" variant="outline" @click="copyPrompt(selected?.prompt || '')">
            <Icon icon="lucide:copy" />
            复制提示词
          </Button>
          <Button size="md" variant="primary" root-class="detail-use-button" @click="usePromptInWorkbench">
            <Icon icon="lucide:wand-sparkles" />
            在创作台使用
          </Button>
        </footer>
      </aside>
    </div>

    <div v-if="selectedImage" class="image-viewer-layer" role="presentation" @mousedown.self="closeImageViewer">
      <section class="image-viewer" role="dialog" aria-modal="true" :aria-label="selectedImage.name">
        <div class="viewer-canvas">
          <img :src="selectedImage.url" :alt="selectedImage.name" />
          <Button size="md" variant="ghost" icon-only root-class="viewer-close" title="关闭" @click="closeImageViewer">
            <Icon icon="lucide:x" />
          </Button>
        </div>
        <aside class="viewer-details">
          <div class="viewer-heading">
            <span class="eyebrow">{{ imageViewerSource === 'prompt' ? '提示词预览' : '作品详情' }}</span>
            <h2>{{ selectedImage.name }}</h2>
            <p v-if="imageViewerSource === 'prompt'">
              {{ selected?.source_name || '提示词库' }}<template v-if="selected && categoryLabel(selected)"> · {{ categoryLabel(selected) }}</template>
            </p>
            <p v-else><template v-if="pixelDimensions(selectedImage)">{{ pixelDimensions(selectedImage) }} · </template>{{ formatBytes(selectedImage.bytes) }} · {{ formatDate(selectedImage.created_at) }}</p>
          </div>
          <dl v-if="imageViewerSource === 'gallery'" class="viewer-meta">
            <template v-if="selectedImage.model"><dt>模型</dt><dd>{{ selectedImage.model }}</dd></template>
            <template v-if="selectedImage.mode"><dt>模式</dt><dd>{{ modeLabel(selectedImage.mode) }}</dd></template>
            <template v-if="selectedImage.duration_seconds"><dt>耗时</dt><dd>{{ selectedImage.duration_seconds }} 秒</dd></template>
            <template v-if="selectedImage.relative_path"><dt>文件</dt><dd><code>{{ selectedImage.relative_path }}</code></dd></template>
          </dl>
          <div v-if="selectedImage.prompt" class="viewer-prompt">
            <div class="viewer-prompt-header">
              <div class="viewer-prompt-title">
                <h3>提示词</h3>
                <span>{{ promptLength(selectedImage.prompt) }}</span>
              </div>
              <Button size="xs" variant="outline" root-class="viewer-copy-button" @click="copyPrompt(selectedImage.prompt)">
                <Icon icon="lucide:copy" />
                复制
              </Button>
            </div>
            <div class="viewer-prompt-body">{{ selectedImage.prompt }}</div>
          </div>
          <div class="viewer-actions">
            <a class="ui-btn ui-btn-md ui-btn-primary viewer-download" :href="viewerDownloadUrl" :download="selectedImage.name">
              <Icon icon="lucide:download" />
              下载原图
            </a>
            <Button
              v-if="imageViewerSource === 'gallery'"
              size="md"
              variant="outline"
              root-class="viewer-wide-action danger-button"
              :disabled="galleryActionBusy"
              @click="runGalleryAction('delete', selectedImage.id)"
            >
              <Icon icon="lucide:trash-2" />删除作品
            </Button>
          </div>
        </aside>
      </section>
    </div>

    <div v-if="connectionOpen" class="drawer-layer connection-layer" role="presentation" @mousedown.self="closeConnection">
      <aside class="connection-drawer" role="dialog" aria-modal="true" aria-label="工作台设置">
        <header class="connection-header">
          <div>
            <span class="eyebrow">SETTINGS</span>
            <h2>工作台设置</h2>
          </div>
          <div class="connection-header-actions">
            <Button v-if="settingsSection === 'connections'" size="sm" variant="outline" @click="startNewConnection">
              <Icon icon="lucide:plus" />添加连接
            </Button>
            <Button size="md" variant="ghost" icon-only root-class="drawer-close" title="关闭" @click="closeConnection">
              <Icon icon="lucide:x" />
            </Button>
          </div>
        </header>
        <nav class="settings-section-tabs" aria-label="设置分类">
          <button type="button" :class="{ active: settingsSection === 'connections' }" @click="selectSettingsSection('connections')">
            <Icon icon="lucide:plug-zap" /><span>连接</span>
          </button>
          <button type="button" :class="{ active: settingsSection === 'storage' }" @click="selectSettingsSection('storage')">
            <Icon icon="lucide:hard-drive" /><span>存储</span>
          </button>
        </nav>

        <form v-if="settingsSection === 'connections'" class="connection-form" @submit.prevent="saveConnection">
          <div class="connection-manager">
            <aside class="connection-list-panel" aria-label="连接配置列表">
              <div class="connection-list-heading">
                <span>连接配置</span><small>{{ connections.length }}</small>
              </div>
              <div v-if="connections.length" class="connection-list">
                <button
                  v-for="connection in connections"
                  :key="connection.id"
                  type="button"
                  class="connection-profile"
                  :class="{ selected: editingConnectionId === connection.id, active: settingsStatus.active_connection_id === connection.id }"
                  @click="selectConnectionEditor(connection.id)"
                >
                  <span class="connection-profile-icon"><Icon :icon="connection.readonly ? 'lucide:terminal-square' : 'lucide:key-round'" /></span>
                  <span class="connection-profile-copy">
                    <strong>{{ connection.name }}</strong>
                    <small>{{ connection.key_hint || '尚未配置 Key' }}</small>
                  </span>
                  <span class="connection-profile-state" :class="{ ready: connection.key_configured }"></span>
                  <span v-if="settingsStatus.active_connection_id === connection.id" class="connection-current-mark">当前</span>
                </button>
              </div>
              <button type="button" class="connection-add-row" @click="startNewConnection">
                <Icon icon="lucide:plus" /><span>添加另一个连接</span>
              </button>
            </aside>

            <section class="connection-editor">
              <header class="connection-editor-header">
                <div>
                  <span>{{ editingConnection ? '连接详情' : '新建连接' }}</span>
                  <h3>{{ connectionForm.name || '未命名连接' }}</h3>
                </div>
                <div class="connection-editor-status">
                  <MetaChip
                    v-if="editingConnection"
                    :tone="editingConnection.key_configured ? 'success' : 'warning'"
                    variant="soft"
                    size="sm"
                  >
                    {{ editingConnection.key_configured ? 'Key 可用' : '缺少 Key' }}
                  </MetaChip>
                  <Button
                    v-if="editingConnection && settingsStatus.active_connection_id !== editingConnection.id"
                    type="button"
                    size="sm"
                    variant="outline"
                    @click="activateConnection(editingConnection.id)"
                  >
                    <Icon icon="lucide:check" />设为当前
                  </Button>
                </div>
              </header>

              <div class="connection-editor-scroll">
                <div v-if="editingConnection?.readonly" class="environment-notice">
                  <Icon icon="lucide:lock-keyhole" />
                  <span><strong>环境变量连接</strong><small>由启动进程提供，需在系统环境变量中修改。</small></span>
                </div>

                <div class="connection-fields">
                  <FormField label="连接名称">
                    <Input v-model="connectionForm.name" size="md" block :disabled="editingConnection?.readonly" placeholder="例如：主账号" />
                  </FormField>
                  <FormField label="API 地址">
                    <Input v-model="connectionForm.base_url" size="md" block :disabled="editingConnection?.readonly" placeholder="https://api.klong.lat" />
                  </FormField>
                  <FormField label="API Key">
                    <Input
                      v-model="connectionForm.api_key"
                      type="password"
                      size="md"
                      block
                      :disabled="editingConnection?.readonly"
                      :placeholder="editingConnection?.key_configured ? `${editingConnection.key_hint} · 留空保持不变` : 'sk-...'"
                      autocomplete="new-password"
                    />
                  </FormField>
                  <FormField label="默认模型" :hint="`${connectionEditorModels.length} 个候选模型`">
                    <FilterSelect
                      v-model="connectionForm.default_model"
                      class="connection-model-select"
                      :options="connectionModelOptions"
                      size="md"
                      placement="down"
                      selected-indicator="check"
                      :disabled="editingConnection?.readonly"
                      aria-label="默认生成模型"
                    />
                  </FormField>
                </div>

                <div class="connection-models-summary">
                  <span class="connection-models-icon"><Icon icon="lucide:boxes" /></span>
                  <span>
                    <strong>{{ testedModels.length ? '模型列表刚刚同步' : '可用模型' }}</strong>
                    <small>{{ testedModels.length || editingConnection?.models?.length || 0 }} 个已从当前连接获取</small>
                  </span>
                  <Icon v-if="testedModels.length" icon="lucide:circle-check" class="connection-models-check" />
                </div>
              </div>

              <footer class="connection-footer">
                <Button
                  v-if="editingConnection && !editingConnection.readonly"
                  type="button"
                  size="md"
                  variant="ghost"
                  root-class="connection-delete-button"
                  :disabled="savingConnection || testingConnection"
                  title="删除连接"
                  @click="deleteConnection"
                >
                  <Icon icon="lucide:trash-2" />
                </Button>
                <span v-else></span>
                <Button type="button" size="md" variant="outline" :disabled="testingConnection || savingConnection" @click="testConnection">
                  <Icon :icon="testingConnection ? 'lucide:loader-circle' : 'lucide:refresh-cw'" :class="{ spin: testingConnection }" />
                  {{ testingConnection ? '同步中' : '同步模型' }}
                </Button>
                <Button
                  v-if="!editingConnection?.readonly"
                  type="submit"
                  size="md"
                  variant="primary"
                  :disabled="savingConnection || testingConnection"
                >
                  <Icon :icon="savingConnection ? 'lucide:loader-circle' : 'lucide:save'" :class="{ spin: savingConnection }" />
                  {{ savingConnection ? '保存中' : editingConnection ? '保存更改' : '添加连接' }}
                </Button>
              </footer>
            </section>
          </div>
        </form>

        <section v-else class="storage-manager">
          <template v-if="runtimeMode === 'browser'">
            <header class="storage-summary browser-storage-summary">
              <span class="storage-summary-icon"><Icon icon="lucide:database-zap" /></span>
              <span class="storage-summary-copy">
                <small>当前数据空间</small>
                <strong>此浏览器</strong>
              </span>
              <MetaChip tone="success" variant="soft" size="sm">仅此设备</MetaChip>
            </header>

            <div class="storage-content browser-storage-content">
              <section class="browser-storage-notice">
                <Icon icon="lucide:shield-check" />
                <span>
                  <strong>Key、作品和历史不会上传到 Vercel</strong>
                  <small>数据由当前浏览器独立保存。同一网址的其他用户、其他浏览器和其他设备都无法读取。</small>
                </span>
              </section>

              <section class="storage-metrics" aria-label="浏览器图库统计">
                <div>
                  <Icon icon="lucide:images" />
                  <span><strong>{{ storageStatus.image_count.toLocaleString() }}</strong><small>张作品</small></span>
                </div>
                <div>
                  <Icon icon="lucide:database" />
                  <span><strong>{{ formatBytes(storageStatus.total_bytes) }}</strong><small>图片占用</small></span>
                </div>
                <div>
                  <Icon icon="lucide:archive" />
                  <span><strong>{{ storageStatus.quota_bytes ? formatBytes(storageStatus.quota_bytes) : '自动' }}</strong><small>浏览器配额</small></span>
                </div>
              </section>

              <section class="browser-persistence-row">
                <span class="browser-persistence-icon" :class="{ ready: storageStatus.browser_persisted }">
                  <Icon :icon="storageStatus.browser_persisted ? 'lucide:badge-check' : 'lucide:clock-alert'" />
                </span>
                <span>
                  <strong>{{ storageStatus.browser_persisted ? '持久存储已启用' : '由浏览器自动管理' }}</strong>
                  <small>{{ storageStatus.browser_persisted ? '浏览器不会因空间不足自动清理这些数据。' : '可申请持久存储，是否授予由浏览器决定。' }}</small>
                </span>
                <Button
                  v-if="!storageStatus.browser_persisted"
                  type="button"
                  size="sm"
                  variant="outline"
                  :disabled="storageBusy"
                  @click="persistBrowserStorage"
                >
                  <Icon icon="lucide:pin" />申请保留
                </Button>
              </section>
            </div>

            <footer class="storage-footer browser-storage-footer">
              <span>删除图库与历史不会影响已保存的连接和 API Key。</span>
              <Button
                type="button"
                size="md"
                variant="outline"
                root-class="danger-button"
                :disabled="storageBusy || (!storageStatus.image_count && !historyTotal)"
                @click="clearBrowserWorkspace"
              >
                <Icon icon="lucide:trash-2" />删除图库与历史
              </Button>
            </footer>
          </template>

          <template v-else>
          <header class="storage-summary">
            <span class="storage-summary-icon"><Icon icon="lucide:folder-kanban" /></span>
            <span class="storage-summary-copy">
              <small>当前图库</small>
              <strong>{{ storageFolderName }}</strong>
            </span>
            <MetaChip :tone="storageStatus.locked ? 'warning' : 'neutral'" variant="soft" size="sm">
              {{ storageSourceLabel }}
            </MetaChip>
          </header>

          <div class="storage-content">
            <section class="storage-location-section">
              <header>
                <span>图库位置</span>
                <small>{{ storageStatus.locked ? '启动配置控制' : '本机绝对路径' }}</small>
              </header>
              <div class="storage-path-row">
                <Icon icon="lucide:folder" />
                <input
                  v-model="storageDraftPath"
                  class="storage-path-input"
                  :disabled="storageStatus.locked || storageBusy"
                  aria-label="图库位置"
                  spellcheck="false"
                />
                <Button
                  v-if="!storageStatus.locked"
                  type="button"
                  size="sm"
                  variant="outline"
                  :disabled="storageBusy"
                  @click="chooseStorageFolder"
                >
                  <Icon icon="lucide:folder-search" />选择
                </Button>
              </div>
              <div class="storage-default-row">
                <span>默认位置</span>
                <code>{{ storageStatus.default_output_dir }}</code>
              </div>
            </section>

            <section class="storage-metrics" aria-label="图库统计">
              <div>
                <Icon icon="lucide:images" />
                <span><strong>{{ storageStatus.image_count.toLocaleString() }}</strong><small>张作品</small></span>
              </div>
              <div>
                <Icon icon="lucide:database" />
                <span><strong>{{ formatBytes(storageStatus.total_bytes) }}</strong><small>图片占用</small></span>
              </div>
            </section>
          </div>

          <footer class="storage-footer">
            <Button type="button" size="md" variant="outline" :disabled="storageBusy" @click="openStorageFolder">
              <Icon icon="lucide:folder-open" />打开目录
            </Button>
            <span></span>
            <Button
              v-if="!storageStatus.locked"
              type="button"
              size="md"
              variant="ghost"
              :disabled="storageBusy || storageStatus.source === 'default'"
              @click="resetStorageFolder"
            >
              恢复默认
            </Button>
            <Button
              v-if="!storageStatus.locked"
              type="button"
              size="md"
              variant="primary"
              :disabled="storageBusy || !storageDirty"
              @click="saveStorageFolder"
            >
              <Icon :icon="storageBusy ? 'lucide:loader-circle' : 'lucide:save'" :class="{ spin: storageBusy }" />保存位置
            </Button>
          </footer>
          </template>
        </section>
      </aside>
    </div>

    <Toast :toasts="toasts" @remove="removeToast" />
  </div>
</template>

<script setup lang="ts">
import { Icon } from '@iconify/vue'
import { Button as NanocatButton, EmptyState, FilterSelect as NanocatFilterSelect, FormField, Input, MetaChip, Toast } from 'nanocat-ui'
import type { SelectOption, ToastItem } from 'nanocat-ui'
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { createStudioRuntime, type RuntimeMode, type StudioRuntime } from './studioRuntime'

// nanocat-ui 0.1.x accepts these runtime variants/emit shapes but its declarations are narrower.
const Button = NanocatButton as any
const FilterSelect = NanocatFilterSelect as any

type Prompt = {
  id: string
  title: string
  prompt: string
  description?: string
  preview?: string
  category?: string
  sub_category?: string
  source_id?: string
  source_name?: string
  image_model?: string
  image_size?: string
  image_count?: number
}

type Job = Record<string, any>
type JobSummary = {
  id: string
  name: string
  status: string
  created_at: string
  completed_at?: string
  model: string
  count: number
  concurrency: number
  succeeded?: number
  failed?: number
  duration_seconds?: number
  thumbnail_url?: string
}
type GalleryItem = {
  id: string
  name: string
  relative_path?: string
  bytes: number
  created_at: string
  url: string
  prompt?: string
  model?: string
  protocol?: string
  mode?: string
  width?: number
  height?: number
  duration_seconds?: number
}
type ConnectionProfile = {
  id: string
  name: string
  base_url: string
  default_model: string
  models: string[]
  models_synced_at?: string
  key_configured: boolean
  key_source: 'environment' | 'secure_storage' | 'session' | 'browser' | 'none'
  key_hint: string
  persistent_secret_storage: boolean
  readonly: boolean
}
type SettingsSection = 'connections' | 'storage'
type StorageStatus = {
  output_dir: string
  default_output_dir: string
  source: 'default' | 'saved' | 'environment' | 'command' | 'browser'
  locked: boolean
  image_count: number
  total_bytes: number
  browser_persisted?: boolean
  quota_bytes?: number
  usage_bytes?: number
}
type ToastTone = ToastItem['type']
type GalleryAction = 'delete'

const PAGE_SIZE = 24
const token = document.querySelector<HTMLMetaElement>('meta[name="klong-token"]')?.content || ''
const runtimeMode = ref<RuntimeMode>('local')
let studioRuntime: StudioRuntime | null = null
let runtimePromise: Promise<StudioRuntime> | null = null
const builtInModels = [
  'gpt-image-2',
  'gpt-image-2-c',
  'gpt-image-2-codex',
  'gpt-image-2-vip',
  'gemini-3-pro-image-preview',
  'gemini-3.1-flash-image-preview',
]
const sourceDisplay: Record<string, { badge: string; name: string }> = {
  'banana-prompt-quicker': { badge: 'B', name: 'Banana' },
  'awesome-gpt-image': { badge: 'A', name: 'GPT Image' },
  'awesome-gpt4o-image-prompts': { badge: '4o', name: 'GPT-4o' },
  'youmind-gpt-image-2': { badge: 'Y', name: 'YouMind' },
  'youmind-nano-banana-pro': { badge: 'N', name: 'Nano' },
  'davidwu-gpt-image2-prompts': { badge: 'D', name: 'DavidWu' },
}

const library = reactive<any>({ sources: [], syncing: true, prompt_count: 0, synced_at: '' })
type WorkspaceView = 'prompts' | 'create' | 'gallery'
type ColorTheme = 'light' | 'dark'

const themeStorageKey = 'klong-prompt-studio-theme'
const colorTheme = ref<ColorTheme>(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
document.documentElement.dataset.theme = colorTheme.value

const activeView = ref<WorkspaceView>('prompts')
const prompts = ref<Prompt[]>([])
const promptTotal = ref(0)
const categories = ref<string[]>([])
const hasMore = ref(true)
const loadingPrompts = ref(false)
const loadError = ref('')
const loadSentinel = ref<HTMLElement | null>(null)
const filters = reactive({ keyword: '', source: 'all', category: 'all' })
const galleryItems = ref<GalleryItem[]>([])
const galleryTotal = ref(0)
const galleryKeyword = ref('')
const gallerySort = ref('created_desc')
const galleryPageSize = ref('24')
const galleryPage = ref(1)
const galleryPageCount = ref(1)
const loadingGallery = ref(false)
const galleryError = ref('')
const gallerySelectedIds = reactive(new Set<string>())
const galleryExcludedIds = reactive(new Set<string>())
const galleryAllResultsSelected = ref(false)
const galleryMutationBusy = ref(false)
const galleryArchiveBusy = ref(false)
const selectedImage = ref<GalleryItem | null>(null)
const imageViewerSource = ref<'gallery' | 'prompt'>('gallery')
const broken = reactive(new Set<string>())
const settingsOpen = ref(false)
const connectionOpen = ref(false)
const settingsSection = ref<SettingsSection>('connections')
const selected = ref<Prompt | null>(null)
const inputFile = ref<File | null>(null)
const inputFileControl = ref<HTMLInputElement | null>(null)
const inputPreviewUrl = ref('')
const submitting = ref(false)
const job = ref<Job | null>(null)
const jobHistory = ref<JobSummary[]>([])
const historyTotal = ref(0)
const startedAt = ref(0)
const elapsed = ref(0)
const toasts = ref<ToastItem[]>([])
const form = reactive<any>({ prompt: '', connection_id: '', model: 'gpt-image-2', size: '', filename: 'generated', count: 1, concurrency: 1 })
const settingsStatus = reactive<any>({
  active_connection_id: '',
  active_connection: null,
  connections: [],
  base_url: 'https://api.klong.lat',
  default_model: 'gpt-image-2',
  key_configured: false,
  key_source: 'none',
  key_hint: '',
  persistent_secret_storage: true,
  models: [],
})
const editingConnectionId = ref('')
const connectionForm = reactive({ name: '', base_url: 'https://api.klong.lat', api_key: '', default_model: 'gpt-image-2' })
const testedModels = ref<string[]>([])
const testingConnection = ref(false)
const savingConnection = ref(false)
const storageStatus = reactive<StorageStatus>({
  output_dir: '',
  default_output_dir: '',
  source: 'default',
  locked: false,
  image_count: 0,
  total_bytes: 0,
})
const storageDraftPath = ref('')
const storageBusy = ref(false)

let observer: IntersectionObserver | null = null
let filterTimer: ReturnType<typeof setTimeout> | undefined
let galleryFilterTimer: ReturnType<typeof setTimeout> | undefined
let libraryTimer: ReturnType<typeof setTimeout> | undefined
let requestVersion = 0
let galleryRequestVersion = 0
let toastSequence = 0
const pollingJobIds = new Set<string>()

const readySources = computed(() => library.sources.filter((source: any) => Number(source.count) > 0).length)
const syncLabel = computed(() => library.syncing ? '同步中' : '已同步')
const connections = computed<ConnectionProfile[]>(() => settingsStatus.connections || [])
const activeConnection = computed<ConnectionProfile | null>(() => (
  connections.value.find((item) => item.id === settingsStatus.active_connection_id)
  || settingsStatus.active_connection
  || null
))
const generationConnection = computed<ConnectionProfile | null>(() => (
  connections.value.find((item) => item.id === form.connection_id) || activeConnection.value
))
const editingConnection = computed<ConnectionProfile | null>(() => (
  connections.value.find((item) => item.id === editingConnectionId.value) || null
))
const connectionLabel = computed(() => activeConnection.value?.name || '未配置连接')
const connectionStateLabel = computed(() => activeConnection.value?.key_configured ? '连接可用' : '缺少 API Key')
const storageSourceLabel = computed(() => ({
  default: '默认位置',
  saved: '自定义位置',
  environment: '环境变量',
  command: '启动参数',
  browser: '浏览器存储',
}[storageStatus.source]))
const storageFolderName = computed(() => {
  const parts = storageStatus.output_dir.split(/[\\/]/).filter(Boolean)
  return parts.at(-1) || storageStatus.output_dir || '尚未加载'
})
const storageDirty = computed(() => (
  storageDraftPath.value.trim() !== storageStatus.output_dir.trim()
))
const keySourceLabel = computed(() => {
  const source = editingConnection.value?.key_source
  if (source === 'environment') return '由 KLONG_API_KEY 与 KLONG_BASE_URL 提供，只读'
  if (source === 'secure_storage') return 'Key 已由 Windows 当前用户加密保存'
  if (source === 'browser') return 'Key 已加密保存在当前浏览器，不会上传到小恐龙或 Vercel'
  if (source === 'session') return 'Key 仅在本次运行中有效'
  return '填写 Key 后即可直接生成图片'
})
const hasFilters = computed(() => Boolean(filters.keyword.trim() || filters.source !== 'all' || filters.category !== 'all'))
const activeSearch = computed({
  get: () => activeView.value === 'prompts' ? filters.keyword : activeView.value === 'gallery' ? galleryKeyword.value : '',
  set: (value: string) => {
    if (activeView.value === 'prompts') filters.keyword = value
    else if (activeView.value === 'gallery') galleryKeyword.value = value
  },
})
const activeSectionLabel = computed(() => {
  if (filters.category !== 'all') return filters.category
  if (filters.source !== 'all') {
    return library.sources.find((source: any) => source.id === filters.source)?.name || '当前词源'
  }
  return '全部提示词'
})
const workspaceParentLabel = computed(() => (
  activeView.value === 'prompts' ? '提示词库' : activeView.value === 'create' ? '图像创作' : '我的图库'
))
const workspaceCurrentLabel = computed(() => {
  if (activeView.value === 'prompts') return activeSectionLabel.value
  if (activeView.value === 'gallery') return '全部作品'
  if (job.value) return historyItemTitle(summaryFromJob(job.value))
  return selected.value?.title || '新建创作'
})
const mobileViewLabel = computed(() => (
  activeView.value === 'prompts' ? '提示词' : activeView.value === 'create' ? '创作' : '图库'
))
const sourceOptions = computed<SelectOption[]>(() => [
  { label: '全部来源', value: 'all' },
  ...library.sources.map((source: any) => ({
    label: source.count ? `${source.name} (${source.count})` : source.name,
    value: source.id,
  })),
])
const categoryOptions = computed<SelectOption[]>(() => [
  { label: '全部分类', value: 'all' },
  ...categories.value.map((category) => ({ label: category, value: category })),
])
const gallerySortOptions: SelectOption[] = [
  { label: '最新生成', value: 'created_desc' },
  { label: '最早生成', value: 'created_asc' },
  { label: '名称 A-Z', value: 'name_asc' },
  { label: '名称 Z-A', value: 'name_desc' },
  { label: '文件从大到小', value: 'size_desc' },
  { label: '文件从小到大', value: 'size_asc' },
]
const galleryPageSizeOptions: SelectOption[] = [
  { label: '每页 12 张', value: '12' },
  { label: '每页 24 张', value: '24' },
  { label: '每页 48 张', value: '48' },
]
const connectionOptions = computed<SelectOption[]>(() => connections.value.map((connection) => ({
  label: `${connection.name}${connection.key_configured ? '' : ' · 未配置 Key'}`,
  value: connection.id,
})))
function imageModelIds(models: string[]) {
  const unique = Array.from(new Set(models.filter(Boolean)))
  const imagePattern = /(?:^|[-_.\/])(image|images|imagen|flux|recraft|dall[-_.]?e|stable[-_.]?diffusion|sdxl|midjourney|banana)(?:[-_.\/]|$)/i
  const filtered = unique.filter((model) => imagePattern.test(model))
  return filtered.length ? filtered : unique
}
const allModels = computed(() => Array.from(new Set([
  ...imageModelIds(generationConnection.value?.models?.length ? generationConnection.value.models : builtInModels),
  form.model,
  generationConnection.value?.default_model,
].filter(Boolean))))
const modelOptions = computed<SelectOption[]>(() => allModels.value.map((model) => ({ label: model, value: model })))
const connectionEditorModels = computed(() => Array.from(new Set([
  ...imageModelIds(
    testedModels.value.length
      ? testedModels.value
      : editingConnection.value?.models?.length
        ? editingConnection.value.models
        : builtInModels,
  ),
  connectionForm.default_model,
].filter(Boolean))))
const connectionModelOptions = computed<SelectOption[]>(() => connectionEditorModels.value.map((model) => ({ label: model, value: model })))
const serialModel = computed(() => ['gpt-image-2-codex', 'gpt-image-2-vip'].includes(form.model))
const isGemini = computed(() => String(form.model).startsWith('gemini-'))
const jobPercent = computed(() => {
  if (!job.value) return 0
  if (job.value.status === 'completed') return 100
  const done = (job.value.result?.succeeded || 0) + (job.value.result?.failed || 0)
  return Math.max(5, Math.round(done / Math.max(1, Number(job.value.count)) * 100))
})
const jobTone = computed<ToastTone>(() => {
  if (job.value?.status === 'completed') return 'success'
  if (job.value?.status === 'failed') return 'error'
  return 'warning'
})
const galleryActionBusy = computed(() => galleryMutationBusy.value || galleryArchiveBusy.value)
const galleryPageSelected = computed(() => (
  galleryItems.value.length > 0 && galleryItems.value.every((item) => isGallerySelected(item.id))
))
const gallerySelectionCount = computed(() => (
  galleryAllResultsSelected.value
    ? Math.max(0, galleryTotal.value - galleryExcludedIds.size)
    : gallerySelectedIds.size
))
const galleryPageNumbers = computed<Array<number | string>>(() => {
  const total = galleryPageCount.value
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
  const pages: Array<number | string> = [1]
  const start = Math.max(2, galleryPage.value - 1)
  const end = Math.min(total - 1, galleryPage.value + 1)
  if (start > 2) pages.push('start-gap')
  for (let page = start; page <= end; page += 1) pages.push(page)
  if (end < total - 1) pages.push('end-gap')
  pages.push(total)
  return pages
})

async function getRuntime() {
  if (studioRuntime) return studioRuntime
  runtimePromise ||= createStudioRuntime(token)
  studioRuntime = await runtimePromise
  runtimeMode.value = studioRuntime.mode
  return studioRuntime
}

async function api(path: string, options: RequestInit = {}) {
  return (await getRuntime()).request(path, options)
}

async function loadSettings() {
  try {
    const data = await api('/api/settings')
    Object.assign(settingsStatus, data)
    form.connection_id = data.active_connection_id || ''
    if (!selected.value && !job.value) form.model = data.active_connection?.default_model || data.default_model || form.model
  } catch (error: any) {
    showToast('error', error.message)
  }
}

function applyStorageStatus(data: Partial<StorageStatus>) {
  Object.assign(storageStatus, data)
  storageDraftPath.value = storageStatus.output_dir
}

async function loadStorage() {
  try {
    applyStorageStatus(await api('/api/storage'))
  } catch (error: any) {
    showToast('error', error.message)
  }
}

function openConnection() {
  settingsSection.value = 'connections'
  if (settingsStatus.active_connection_id) selectConnectionEditor(settingsStatus.active_connection_id)
  else startNewConnection()
  connectionOpen.value = true
}

function closeConnection() {
  connectionOpen.value = false
}

function selectSettingsSection(section: SettingsSection) {
  settingsSection.value = section
  if (section === 'storage') storageDraftPath.value = storageStatus.output_dir
}

async function refreshStorageViews() {
  clearGallerySelection()
  selectedImage.value = null
  job.value = null
  jobHistory.value = []
  historyTotal.value = 0
  pollingJobIds.clear()
  await Promise.all([resetGallery(), loadJobHistory(true)])
}

async function storageAction(payload: Record<string, unknown>) {
  return api('/api/storage', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

async function chooseStorageFolder() {
  try {
    storageBusy.value = true
    const data = await storageAction({ action: 'pick' })
    applyStorageStatus(data)
    if (data.selected_path) storageDraftPath.value = data.selected_path
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    storageBusy.value = false
  }
}

async function saveStorageFolder() {
  const nextPath = storageDraftPath.value.trim()
  if (!nextPath || !storageDirty.value) return
  if (!window.confirm('切换图库位置后，原位置的作品不会自动移动。继续保存吗？')) return
  try {
    storageBusy.value = true
    applyStorageStatus(await storageAction({ action: 'set', output_dir: nextPath }))
    await refreshStorageViews()
    showToast('success', '图库位置已更新')
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    storageBusy.value = false
  }
}

async function resetStorageFolder() {
  if (!window.confirm('恢复默认图库位置？原位置的作品不会自动移动。')) return
  try {
    storageBusy.value = true
    applyStorageStatus(await storageAction({ action: 'reset' }))
    await refreshStorageViews()
    showToast('success', '已恢复默认图库位置')
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    storageBusy.value = false
  }
}

async function openStorageFolder() {
  try {
    storageBusy.value = true
    await storageAction({ action: 'open' })
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    storageBusy.value = false
  }
}

async function persistBrowserStorage() {
  try {
    storageBusy.value = true
    applyStorageStatus(await storageAction({ action: 'persist' }))
    showToast(storageStatus.browser_persisted ? 'success' : 'warning', storageStatus.browser_persisted ? '浏览器已允许持久保留工作台数据' : '浏览器未授予持久存储权限')
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    storageBusy.value = false
  }
}

async function clearBrowserWorkspace() {
  if (!window.confirm('永久删除此浏览器中的全部作品和生成历史？连接配置与 API Key 会保留。')) return
  try {
    storageBusy.value = true
    applyStorageStatus(await storageAction({ action: 'clear' }))
    await refreshStorageViews()
    showToast('success', '此浏览器中的图库与生成历史已清空')
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    storageBusy.value = false
  }
}

function fillConnectionForm(connection: ConnectionProfile | null) {
  connectionForm.name = connection?.name || ''
  connectionForm.base_url = connection?.base_url || 'https://api.klong.lat'
  connectionForm.api_key = ''
  connectionForm.default_model = connection?.default_model || 'gpt-image-2'
  testedModels.value = []
}

function selectConnectionEditor(connectionId: string) {
  const connection = connections.value.find((item) => item.id === connectionId) || null
  editingConnectionId.value = connection?.id || ''
  fillConnectionForm(connection)
}

function startNewConnection() {
  editingConnectionId.value = ''
  fillConnectionForm(null)
  connectionForm.name = connections.value.length ? `连接 ${connections.value.length + 1}` : '默认连接'
}

function applySettings(data: any, useDefaultModel = false) {
  Object.assign(settingsStatus, data)
  form.connection_id = data.active_connection_id || ''
  const active = data.active_connection || data.connections?.find((item: ConnectionProfile) => item.id === data.active_connection_id)
  if (useDefaultModel || !modelOptions.value.some((option) => option.value === form.model)) {
    form.model = active?.default_model || form.model
  }
}

async function activateConnection(connectionId: string, notify = true) {
  try {
    const data = await api(`/api/connections/${connectionId}/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    applySettings(data, true)
    if (notify) showToast('success', `已切换到 ${data.active_connection?.name || '当前连接'}`)
  } catch (error: any) {
    showToast('error', error.message)
    await loadSettings()
  }
}

async function changeWorkbenchConnection(connectionId: string) {
  if (!connectionId || connectionId === settingsStatus.active_connection_id) return
  await activateConnection(connectionId, false)
}

async function testConnection() {
  try {
    testingConnection.value = true
    const result = await api('/api/settings/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...connectionForm, connection_id: editingConnectionId.value }),
    })
    testedModels.value = result.models || []
    showToast('success', `连接成功，发现 ${result.model_count} 个模型`)
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    testingConnection.value = false
  }
}

async function saveConnection() {
  try {
    savingConnection.value = true
    const isNew = !editingConnectionId.value
    const path = isNew ? '/api/connections' : `/api/connections/${editingConnectionId.value}`
    const data = await api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...connectionForm,
        activate: isNew,
        ...(testedModels.value.length ? { models: testedModels.value } : {}),
      }),
    })
    applySettings(data, isNew || editingConnectionId.value === data.active_connection_id)
    const saved = isNew
      ? data.active_connection
      : data.connections?.find((item: ConnectionProfile) => item.id === editingConnectionId.value)
    selectConnectionEditor(saved?.id || data.active_connection_id)
    showToast('success', isNew ? '连接已添加并设为当前' : '连接设置已保存')
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    savingConnection.value = false
  }
}

async function deleteConnection() {
  const connection = editingConnection.value
  if (!connection || connection.readonly) return
  if (!window.confirm(`永久删除连接“${connection.name}”？`)) return
  try {
    savingConnection.value = true
    const data = await api(`/api/connections/${connection.id}/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    applySettings(data, true)
    if (data.active_connection_id) selectConnectionEditor(data.active_connection_id)
    else startNewConnection()
    showToast('success', '连接已删除')
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    savingConnection.value = false
  }
}

async function loadLibrary(polling = false) {
  try {
    const wasSyncing = library.syncing
    Object.assign(library, await api('/api/library'))
    if (library.syncing) scheduleLibraryPoll(true, 1400)
    else if (polling && wasSyncing) {
      showToast('success', '提示词库已更新')
      await resetPrompts()
    }
  } catch (error: any) {
    if (!polling) showToast('error', error.message)
    scheduleLibraryPoll(polling, 2500)
  }
}

async function requestGalleryPage(pageNumber: number, version: number) {
  if (version !== galleryRequestVersion) return
  loadingGallery.value = true
  galleryError.value = ''
  const limit = Number(galleryPageSize.value)
  const params = new URLSearchParams({
    offset: String((Math.max(1, pageNumber) - 1) * limit),
    limit: String(limit),
    sort: gallerySort.value,
  })
  if (galleryKeyword.value.trim()) params.set('keyword', galleryKeyword.value.trim())
  try {
    const page = await api(`/api/gallery?${params.toString()}`)
    if (version !== galleryRequestVersion) return
    galleryItems.value = page.items
    galleryTotal.value = page.total
    galleryPage.value = page.page
    galleryPageCount.value = page.page_count
  } catch (error: any) {
    if (version === galleryRequestVersion) galleryError.value = error.message
  } finally {
    if (version === galleryRequestVersion) loadingGallery.value = false
  }
}

async function resetGallery(pageNumber = 1) {
  const version = ++galleryRequestVersion
  galleryItems.value = []
  galleryTotal.value = 0
  galleryPageCount.value = 1
  await requestGalleryPage(pageNumber, version)
}

async function goGalleryPage(pageNumber: number) {
  if (loadingGallery.value || pageNumber < 1 || pageNumber > galleryPageCount.value || pageNumber === galleryPage.value) return
  const version = ++galleryRequestVersion
  await requestGalleryPage(pageNumber, version)
  document.querySelector('.library-content')?.scrollTo({ top: 0, behavior: 'smooth' })
}

function scheduleGalleryReset(delay = 0) {
  if (galleryFilterTimer) clearTimeout(galleryFilterTimer)
  galleryFilterTimer = setTimeout(() => void resetGallery(), delay)
}

async function switchView(view: WorkspaceView) {
  activeView.value = view
  if (view === 'create') await loadJobHistory(true)
  if (view === 'gallery' && !galleryItems.value.length && !loadingGallery.value) await resetGallery()
  await nextTick()
  bindObserver()
}

async function startNewCreation() {
  const defaultModel = generationConnection.value?.default_model
    || activeConnection.value?.default_model
    || settingsStatus.default_model
    || 'gpt-image-2'

  settingsOpen.value = false
  selected.value = null
  job.value = null
  clearInputFile()
  submitting.value = false
  startedAt.value = 0
  elapsed.value = 0
  form.prompt = ''
  form.model = defaultModel
  form.size = ''
  form.filename = 'generated'
  form.count = 1
  form.concurrency = 1
  await switchView('create')
}

function clearGallerySearch() {
  galleryKeyword.value = ''
}

function isGallerySelected(imageId: string) {
  return galleryAllResultsSelected.value
    ? !galleryExcludedIds.has(imageId)
    : gallerySelectedIds.has(imageId)
}

function toggleGalleryItem(imageId: string) {
  if (galleryAllResultsSelected.value) {
    if (galleryExcludedIds.has(imageId)) galleryExcludedIds.delete(imageId)
    else galleryExcludedIds.add(imageId)
    return
  }
  if (gallerySelectedIds.has(imageId)) gallerySelectedIds.delete(imageId)
  else gallerySelectedIds.add(imageId)
}

function toggleGalleryPage() {
  const shouldSelect = !galleryPageSelected.value
  for (const image of galleryItems.value) {
    const selectedNow = isGallerySelected(image.id)
    if (selectedNow !== shouldSelect) toggleGalleryItem(image.id)
  }
}

function selectAllGalleryResults() {
  galleryAllResultsSelected.value = true
  gallerySelectedIds.clear()
  galleryExcludedIds.clear()
}

function clearGallerySelection() {
  galleryAllResultsSelected.value = false
  gallerySelectedIds.clear()
  galleryExcludedIds.clear()
}

function gallerySelectionPayload(singleId = '') {
  if (singleId) {
    return { scope: 'ids', ids: [singleId], exclude_ids: [] }
  }
  return {
    scope: galleryAllResultsSelected.value ? 'query' : 'ids',
    ids: [...gallerySelectedIds],
    keyword: galleryKeyword.value.trim(),
    exclude_ids: [...galleryExcludedIds],
  }
}

async function runGalleryAction(action: GalleryAction, singleId = '') {
  const count = singleId ? 1 : gallerySelectionCount.value
  if (!count) return
  const location = runtimeMode.value === 'browser' ? '此浏览器' : '本地磁盘'
  if (!window.confirm(`确定永久删除 ${count} 张作品吗？图片将从${location}移除，无法恢复。`)) return
  try {
    galleryMutationBusy.value = true
    const payload = { action, ...gallerySelectionPayload(singleId) }
    const result = await api('/api/gallery/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const labels: Record<GalleryAction, string> = {
      delete: '已删除',
    }
    showToast(result.failed ? 'warning' : 'success', `${labels[action]} ${result.affected} 张${result.failed ? `，${result.failed} 张失败` : ''}`)
    if (singleId) closeImageViewer()
    clearGallerySelection()
    await resetGallery(galleryPage.value)
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    galleryMutationBusy.value = false
  }
}

async function downloadGalleryArchive() {
  if (!gallerySelectionCount.value) return
  try {
    galleryArchiveBusy.value = true
    const blob = await (await getRuntime()).archive(gallerySelectionPayload())
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `klong-gallery-${new Date().toISOString().slice(0, 10)}.zip`
    document.body.appendChild(link)
    link.click()
    window.setTimeout(() => {
      link.remove()
      URL.revokeObjectURL(url)
    }, 60_000)
    showToast('success', `已打包 ${gallerySelectionCount.value} 张作品`)
  } catch (error: any) {
    showToast('error', error.message)
  } finally {
    galleryArchiveBusy.value = false
  }
}

function clearActiveSearch() {
  if (activeView.value === 'prompts') filters.keyword = ''
  else if (activeView.value === 'gallery') galleryKeyword.value = ''
}

function scheduleLibraryPoll(polling: boolean, delay: number) {
  if (libraryTimer) clearTimeout(libraryTimer)
  libraryTimer = setTimeout(() => void loadLibrary(polling), delay)
}

async function requestPage(offset: number, version: number) {
  if (version !== requestVersion) return
  loadingPrompts.value = true
  loadError.value = ''
  const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) })
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  if (filters.source !== 'all') params.set('source', filters.source)
  if (filters.category !== 'all') params.set('category', filters.category)

  try {
    const page = await api(`/api/prompts?${params.toString()}`)
    if (version !== requestVersion) return
    prompts.value = offset === 0 ? page.items : [...prompts.value, ...page.items]
    promptTotal.value = page.total
    categories.value = page.categories || []
    hasMore.value = page.has_more
  } catch (error: any) {
    if (version === requestVersion) loadError.value = error.message
  } finally {
    if (version === requestVersion) loadingPrompts.value = false
  }
}

async function resetPrompts() {
  const version = ++requestVersion
  prompts.value = []
  promptTotal.value = 0
  hasMore.value = true
  await requestPage(0, version)
}

async function loadNextPage() {
  if (loadingPrompts.value || !hasMore.value) return
  await requestPage(prompts.value.length, requestVersion)
}

function schedulePromptReset(delay = 0) {
  if (filterTimer) clearTimeout(filterTimer)
  filterTimer = setTimeout(() => void resetPrompts(), delay)
}

async function refreshAll() {
  try {
    await api('/api/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    library.syncing = true
    showToast('info', '已开始更新提示词库')
    scheduleLibraryPoll(true, 500)
  } catch (error: any) {
    showToast('error', error.message)
  }
}

function categoryLabel(item: Prompt) {
  return [item.category, item.sub_category].filter(Boolean).join(' / ')
}

function preview(item: Prompt) {
  return broken.has(item.id) || !item.preview ? '' : studioRuntime?.promptPreview(item) || item.preview
}

function onPreviewError(item: Prompt) {
  broken.add(item.id)
}

function promptLength(value: string) {
  return `${Array.from(String(value || '')).length.toLocaleString()} 字`
}

function imageDimensions(image: GalleryItem) {
  return image.width && image.height ? `${image.width} × ${image.height}` : formatBytes(image.bytes)
}

function pixelDimensions(image: GalleryItem) {
  return image.width && image.height ? `${image.width} × ${image.height}` : ''
}

function formatDate(value: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

function modeLabel(value: string) {
  return value === 'image-to-image' ? '图生图' : value === 'text-to-image' ? '文生图' : value
}

function sourceBadge(sourceId: string) {
  return sourceDisplay[sourceId]?.badge || sourceId.slice(0, 1).toUpperCase()
}

function sourceShortName(source: any) {
  return sourceDisplay[source.id]?.name || source.name
}

function choosePrompt(item: Prompt) {
  selected.value = item
  form.prompt = item.prompt
  form.filename = cleanFilename(item.title) || 'generated'
  if (item.image_model) form.model = item.image_model
  if (item.image_size) form.size = item.image_size
  if (item.image_count) form.count = Math.max(1, Number(item.image_count) || 1)
  normalizeModel()
  job.value = null
  settingsOpen.value = true
}

function closeSettings() {
  if (imageViewerSource.value === 'prompt') closeImageViewer()
  settingsOpen.value = false
}

function openPromptPreview() {
  if (!selected.value) return
  const url = preview(selected.value)
  if (!url) return
  imageViewerSource.value = 'prompt'
  selectedImage.value = {
    id: selected.value.id,
    name: selected.value.title,
    bytes: 0,
    created_at: '',
    url,
    prompt: selected.value.prompt,
    model: selected.value.image_model,
  }
}

async function usePromptInWorkbench() {
  closeSettings()
  await switchView('create')
}

function openGalleryImage(image: GalleryItem) {
  imageViewerSource.value = 'gallery'
  selectedImage.value = image
}

function openGeneratedImage(image: any) {
  if (!image.url) return
  imageViewerSource.value = 'gallery'
  selectedImage.value = {
    id: image.id || String(image.index),
    name: image.output?.split(/[\\/]/).pop() || `生成结果 ${image.index}`,
    bytes: image.bytes || 0,
    created_at: job.value?.completed_at || new Date().toISOString(),
    url: image.url,
    prompt: form.prompt,
    model: job.value?.model || form.model,
    mode: job.value?.result?.mode,
    width: image.width,
    height: image.height,
    duration_seconds: image.duration_seconds,
  }
}

function closeImageViewer() {
  selectedImage.value = null
}

const viewerDownloadUrl = computed(() => {
  const url = selectedImage.value?.url || ''
  return studioRuntime?.downloadUrl(url) || url
})

async function showGalleryFromJob() {
  closeSettings()
  galleryKeyword.value = ''
  clearGallerySelection()
  await switchView('gallery')
  await resetGallery()
}

async function copyPrompt(value = '') {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    showToast('success', '提示词已复制')
  } catch {
    showToast('error', '复制失败，请检查浏览器权限')
  }
}

function clearFilters() {
  filters.keyword = ''
  filters.source = 'all'
  filters.category = 'all'
}

function browseCategory(category: string) {
  filters.keyword = ''
  filters.category = category
}

function browseSource(source: string) {
  filters.keyword = ''
  filters.category = 'all'
  filters.source = source
}

function cleanFilename(value: string) {
  return value.replace(/[\\/:*?"<>|]+/g, '-').trim().slice(0, 60)
}

function normalizeModel() {
  if (isGemini.value) form.size = ''
  form.concurrency = serialModel.value ? 1 : Math.max(1, Number(form.count) || 1)
}

function clearInputFile() {
  if (inputPreviewUrl.value) URL.revokeObjectURL(inputPreviewUrl.value)
  inputPreviewUrl.value = ''
  inputFile.value = null
  if (inputFileControl.value) inputFileControl.value.value = ''
}

function onFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  clearInputFile()
  inputFile.value = file
  if (file) inputPreviewUrl.value = URL.createObjectURL(file)
}

function fileData(file: File | null) {
  if (!file) return Promise.resolve(null)
  if (file.size > 20 * 1024 * 1024) return Promise.reject(new Error('参考图片不能超过 20 MiB'))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

async function createJob() {
  try {
    if (!generationConnection.value?.key_configured) {
      openConnection()
      showToast('warning', '请先为当前连接配置 API Key')
      return
    }
    submitting.value = true
    const count = Math.max(1, Number(form.count) || 1)
    const concurrency = Math.max(1, Number(form.concurrency) || 1)
    if (concurrency > count) throw new Error('并发数不能超过生成数量')
    const payload = { ...form, connection_id: generationConnection.value.id, count, concurrency, input_image: await fileData(inputFile.value) }
    const createdJob: Job = await api('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    job.value = createdJob
    startedAt.value = Date.now()
    elapsed.value = 0
    upsertJobHistory(createdJob)
    startPollingJob(createdJob.id)
  } catch (error: any) {
    showToast('error', error.message)
    submitting.value = false
  }
}

function startPollingJob(jobId: string) {
  if (!jobId || pollingJobIds.has(jobId)) return
  pollingJobIds.add(jobId)
  void pollJob(jobId)
}

async function pollJob(jobId: string) {
  try {
    const updated = await api(`/api/jobs/${jobId}`)
    upsertJobHistory(updated)
    const isActive = job.value?.id === jobId
    if (isActive) {
      job.value = updated
      elapsed.value = Math.round((Date.now() - startedAt.value) / 1000)
    }
    if (['queued', 'running'].includes(updated.status)) {
      setTimeout(() => void pollJob(jobId), 1200)
      return
    }
    pollingJobIds.delete(jobId)
    if (isActive) {
      submitting.value = false
      showToast(updated.status === 'completed' ? 'success' : 'error', updated.status === 'completed' ? '生成任务已完成' : '生成任务失败')
    }
    if (updated.result?.images?.length) void resetGallery()
    void loadJobHistory(true)
  } catch (error: any) {
    pollingJobIds.delete(jobId)
    if (job.value?.id === jobId) {
      showToast('error', error.message)
      submitting.value = false
    }
  }
}

function summaryFromJob(value: Job): JobSummary {
  return {
    id: value.id,
    name: value.name || '未命名任务',
    status: value.status,
    created_at: value.created_at,
    completed_at: value.completed_at,
    model: value.model,
    count: Number(value.count) || 1,
    concurrency: Number(value.concurrency) || 1,
    succeeded: Number(value.result?.succeeded ?? value.succeeded) || 0,
    failed: Number(value.result?.failed ?? value.failed) || 0,
    duration_seconds: Number(value.result?.duration_seconds ?? value.duration_seconds) || 0,
    thumbnail_url: value.result?.images?.find((image: any) => image.url)?.url || value.thumbnail_url || '',
  }
}

function upsertJobHistory(value: Job) {
  const summary = summaryFromJob(value)
  jobHistory.value = [summary, ...jobHistory.value.filter((item) => item.id !== summary.id)]
    .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
    .slice(0, 50)
  historyTotal.value = Math.max(historyTotal.value, jobHistory.value.length)
}

async function loadJobHistory(silent = false) {
  try {
    const data = await api('/api/jobs?limit=50')
    jobHistory.value = data.items || []
    historyTotal.value = Number(data.total) || jobHistory.value.length
    for (const item of jobHistory.value) {
      if (['queued', 'running'].includes(item.status)) startPollingJob(item.id)
    }
  } catch (error: any) {
    if (!silent) showToast('error', error.message)
  }
}

async function restoreHistoryJob(item: JobSummary) {
  try {
    const restored = await api(`/api/jobs/${item.id}`)
    job.value = restored
    selected.value = null
    clearInputFile()
    form.prompt = restored.prompt || ''
    form.model = restored.model || form.model
    form.size = restored.size || ''
    form.filename = restored.name || 'generated'
    form.count = Math.max(1, Number(restored.count) || 1)
    await nextTick()
    form.concurrency = Math.max(1, Number(restored.concurrency) || 1)
    const started = Date.parse(restored.started_at || restored.created_at || '')
    startedAt.value = Number.isNaN(started) ? Date.now() : started
    elapsed.value = Number(restored.result?.duration_seconds) || Math.max(0, Math.round((Date.now() - startedAt.value) / 1000))
    submitting.value = ['queued', 'running'].includes(restored.status)
    if (submitting.value) startPollingJob(restored.id)
  } catch (error: any) {
    showToast('error', error.message)
  }
}

function historyStatusIcon(status: string) {
  if (status === 'completed') return 'lucide:check'
  if (status === 'failed') return 'lucide:x'
  if (status === 'running') return 'lucide:loader-circle'
  return 'lucide:clock-3'
}

function historyItemTitle(item: JobSummary) {
  const name = String(item.name || '').trim()
  if (!name || /^历史任务\s+[a-f0-9]+$/i.test(name)) return `${item.count} 张生成结果`
  return name
}

function statusLabel(value: string) {
  return ({ queued: '排队中', running: '生成中', completed: '已完成', failed: '失败' } as Record<string, string>)[value] || value
}

function formatBytes(value: number) {
  if (!value) return '0 B'
  return value >= 1048576 ? `${(value / 1048576).toFixed(2)} MiB` : `${Math.round(value / 1024)} KiB`
}

function showToast(type: ToastTone, message: string) {
  const id = `${Date.now()}-${++toastSequence}`
  toasts.value.push({ id, type, message })
  setTimeout(() => removeToast(id), 3200)
}

function removeToast(id: string) {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (connectionOpen.value) closeConnection()
  else if (selectedImage.value) closeImageViewer()
  else closeSettings()
}

function toggleColorTheme() {
  colorTheme.value = colorTheme.value === 'dark' ? 'light' : 'dark'
  document.documentElement.dataset.theme = colorTheme.value
  try {
    window.localStorage.setItem(themeStorageKey, colorTheme.value)
  } catch {}
}

watch(() => filters.keyword, () => schedulePromptReset(320))
watch(galleryKeyword, () => {
  clearGallerySelection()
  scheduleGalleryReset(320)
})
watch([gallerySort, galleryPageSize], () => {
  clearGallerySelection()
  scheduleGalleryReset()
})
watch(() => filters.source, () => {
  if (filters.category !== 'all') filters.category = 'all'
  schedulePromptReset()
})
watch(() => filters.category, () => schedulePromptReset())
watch(() => form.count, () => normalizeModel())

function bindObserver() {
  observer?.disconnect()
  const target = activeView.value === 'prompts' ? loadSentinel.value : null
  if (target) observer?.observe(target)
}

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  await getRuntime()
  await Promise.all([loadSettings(), loadStorage()])
  await loadJobHistory()
  await loadLibrary()
  await resetPrompts()
  await nextTick()
  observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      if (activeView.value === 'prompts') void loadNextPage()
    },
    { rootMargin: '500px 0px' },
  )
  bindObserver()
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  observer?.disconnect()
  if (inputPreviewUrl.value) URL.revokeObjectURL(inputPreviewUrl.value)
  if (filterTimer) clearTimeout(filterTimer)
  if (galleryFilterTimer) clearTimeout(galleryFilterTimer)
  if (libraryTimer) clearTimeout(libraryTimer)
  studioRuntime?.dispose()
})
</script>
