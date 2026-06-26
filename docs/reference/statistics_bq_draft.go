//go:build ignore

// 草稿:套餐明细统计(CountPackageDetailProductSale)BigQuery 版
// 仅供审阅,故意放在 ttpos-server-go 仓库之外,且加 //go:build ignore 永不参与编译。
//
// 设计要点(对应前面讨论):
//  1. 只支持这一个场景,不做通用化、不做 SQL 拦截器。
//  2. business_status_period 在 BQ 里没有 → 混合注入:窗口由调用方从 MySQL 读出传进来,
//     在 BQ 的 NOT EXISTS 里拼成 OR 串;窗口为空时整段 NOT EXISTS 省略(与 MySQL 语义等价)。
//  3. flag 默认关;BQ 失败不 fallback MySQL(事故期别把火引回主库)。
//  4. singleflight + 短缓存挡重试风暴/控 BQ 账单。
//  5. dataset = shop<uuid>,表保留 ttpos_ 前缀;companyUuid 必须校验为纯数字再拼 FQN(防注入)。

package bqdraft

import (
	"context"
	"fmt"
	"math/big"
	"strconv"
	"strings"
	"sync"
	"time"

	"cloud.google.com/go/bigquery"
	"golang.org/x/sync/singleflight"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

const bqProjectID = "diyl-407103"

// ---------------------------------------------------------------------------
// 1. 配置 / 开关(实际接进去时从 config.DatabaseConf 或独立 BQConf 读)
// ---------------------------------------------------------------------------

type BQConf struct {
	Enabled         bool   // STATISTICS_BQ_ENABLED,默认 false
	ProjectID       string // diyl-407103
	CredentialsFile string // service account json 路径(或用 ADC)
	QueryTimeout    time.Duration
	CacheTTL        time.Duration
}

// ---------------------------------------------------------------------------
// 2. BQ client 单例(全局复用,别每请求新建)
// ---------------------------------------------------------------------------

var (
	bqOnce   sync.Once
	bqClient *bigquery.Client
	bqInitEr error
)

func getBQClient(ctx context.Context, conf BQConf) (*bigquery.Client, error) {
	bqOnce.Do(func() {
		var opts []option.ClientOption
		if conf.CredentialsFile != "" {
			opts = append(opts, option.WithCredentialsFile(conf.CredentialsFile))
		}
		bqClient, bqInitEr = bigquery.NewClient(ctx, conf.ProjectID, opts...)
	})
	return bqClient, bqInitEr
}

// ---------------------------------------------------------------------------
// 3. 入参 / 出参
// ---------------------------------------------------------------------------

// BusinessStatusPeriod:调用方从 MySQL 读出来传进来
//   SELECT start_time, end_time FROM ttpos_business_status_period WHERE delete_time = 0
type BusinessStatusPeriod struct {
	StartTime int64
	EndTime   int64 // 0 表示无上界
}

// StatisticsProductSaleRow:BQ 结果行。
// 注意:这是草稿用的本地结构;接进去时要映射到 model.StatisticsProductSaleData,
// 字段名/标签按真实 model 对齐。bigquery tag 必须与 SELECT 的别名一一对应。
//
// 类型选择:
//   - uuid 类 → int64(系统 uuid 是 snowflake 大整数,约 1.5e15,int64 够)
//   - 金额 decimal → BQ NUMERIC,Go client 原生反序列化为 *big.Rat,保精度;最后转 decimal/string
//   - 数量 int → int64
type StatisticsProductSaleRow struct {
	PackageSopUUID           int64    `bigquery:"package_sop_uuid"`
	PackageFinalPrice        *big.Rat `bigquery:"package_final_price"`
	PackageTaxFee            *big.Rat `bigquery:"package_tax_fee"`
	PackageServiceFee        *big.Rat `bigquery:"package_service_fee"`
	PackageServiceTax        *big.Rat `bigquery:"package_service_tax"`
	PackageFreeNum           int64    `bigquery:"package_free_num"`
	PackageGiveNum           int64    `bigquery:"package_give_num"`
	PackageProductNum        int64    `bigquery:"package_product_num"`
	PackageRefundNum         int64    `bigquery:"package_refund_num"`
	PackageProductPrice      *big.Rat `bigquery:"package_product_price"`
	ParentProductPackageUUID int64    `bigquery:"parent_product_package_uuid"`
	ParentProductName        string   `bigquery:"parent_product_name"`
	ParentCategoryName       string   `bigquery:"parent_category_name"`
	ParentCategoryParentName string   `bigquery:"parent_category_parent_name"`
	SubProductPackageUUID    int64    `bigquery:"sub_product_package_uuid"`
	SubProductPrice          *big.Rat `bigquery:"sub_product_price"`
	SubSaucePrice            *big.Rat `bigquery:"sub_sauce_price"`
	SubLatestPrice           *big.Rat `bigquery:"sub_latest_price"`
	SubNum                   int64    `bigquery:"sub_num"`
	SubUnitNum               int64    `bigquery:"sub_unit_num"`
	SubCopyNum               int64    `bigquery:"sub_copy_num"`
	SubProductName           string   `bigquery:"sub_product_name"`
	SubFlavorName            string   `bigquery:"sub_flavor_name"`
	SubCategoryName          string   `bigquery:"sub_category_name"`
	SubCategoryParentName    string   `bigquery:"sub_category_parent_name"`
}

// ---------------------------------------------------------------------------
// 4. 主方法
// ---------------------------------------------------------------------------

type StatisticsBQRepo struct {
	conf  BQConf
	sf    singleflight.Group
	cache sync.Map // key -> cacheEntry(生产建议换成带 TTL 淘汰的 cache,如 ristretto)
}

type cacheEntry struct {
	rows    []StatisticsProductSaleRow
	expires time.Time
}

func NewStatisticsBQRepo(conf BQConf) *StatisticsBQRepo {
	return &StatisticsBQRepo{conf: conf}
}

// CountPackageDetailProductSaleBQ:语义对齐 MySQL 版,签名只多了 windows(由调用方从 MySQL 取)。
func (r *StatisticsBQRepo) CountPackageDetailProductSaleBQ(
	ctx context.Context,
	companyUUID uint64,
	start, end int64,
	windows []BusinessStatusPeriod,
) ([]StatisticsProductSaleRow, error) {

	// 4.0 防注入:companyUUID 只能是数字(它本来就是 uint64,这里再保险一道)
	dataset := "shop" + strconv.FormatUint(companyUUID, 10)

	// 4.1 缓存键 = 租户 + 区间 + 窗口指纹
	key := r.cacheKey(companyUUID, start, end, windows)
	if v, ok := r.cache.Load(key); ok {
		if e := v.(cacheEntry); time.Now().Before(e.expires) {
			return e.rows, nil
		}
	}

	// 4.2 singleflight:同 key 并发只打一次 BQ(挡重试风暴)
	res, err, _ := r.sf.Do(key, func() (any, error) {
		rows, err := r.runQuery(ctx, dataset, start, end, windows)
		if err != nil {
			return nil, err
		}
		r.cache.Store(key, cacheEntry{rows: rows, expires: time.Now().Add(r.conf.CacheTTL)})
		return rows, nil
	})
	if err != nil {
		// 关键:BQ 失败不 fallback MySQL。返回错误(或上层取缓存旧值),别把火引回主库。
		return nil, fmt.Errorf("bq package-detail query: %w", err)
	}
	return res.([]StatisticsProductSaleRow), nil
}

func (r *StatisticsBQRepo) runQuery(
	ctx context.Context,
	dataset string,
	start, end int64,
	windows []BusinessStatusPeriod,
) ([]StatisticsProductSaleRow, error) {

	client, err := getBQClient(ctx, r.conf)
	if err != nil {
		return nil, err
	}

	if r.conf.QueryTimeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, r.conf.QueryTimeout)
		defer cancel()
	}

	sql, params := buildSQL(r.conf.ProjectID, dataset, start, end, windows)

	q := client.Query(sql)
	q.Parameters = params

	it, err := q.Read(ctx)
	if err != nil {
		return nil, err
	}

	rows := make([]StatisticsProductSaleRow, 0, 256)
	for {
		var row StatisticsProductSaleRow
		err := it.Next(&row)
		if err == iterator.Done {
			break
		}
		if err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// ---------------------------------------------------------------------------
// 5. SQL 构建(表名拼 FQN;窗口拼 OR 串并参数化;窗口为空则省略 NOT EXISTS)
// ---------------------------------------------------------------------------

func buildSQL(project, dataset string, start, end int64, windows []BusinessStatusPeriod) (string, []bigquery.QueryParameter) {
	t := func(name string) string { // `project.shopX.ttpos_xxx`
		return fmt.Sprintf("`%s.%s.%s`", project, dataset, name)
	}

	params := []bigquery.QueryParameter{
		{Name: "start", Value: start},
		{Name: "end", Value: end},
	}

	// 窗口 → NOT EXISTS 片段;空则整段不加
	var notExists string
	if len(windows) > 0 {
		ors := make([]string, 0, len(windows))
		for i, w := range windows {
			s, e := fmt.Sprintf("w%d_s", i), fmt.Sprintf("w%d_e", i)
			ors = append(ors, fmt.Sprintf(
				"(_sb.create_time >= @%s AND (@%s = 0 OR _sb.create_time <= @%s))", s, e, e))
			params = append(params,
				bigquery.QueryParameter{Name: s, Value: w.StartTime},
				bigquery.QueryParameter{Name: e, Value: w.EndTime},
			)
		}
		notExists = fmt.Sprintf(`
  AND NOT EXISTS (
    SELECT 1 FROM %s AS _sb
    WHERE _sb.uuid = sp.sale_bill_uuid AND _sb.delete_time = 0
      AND ( %s )
  )`, t("ttpos_sale_bill"), strings.Join(ors, " OR "))
	}

	sql := fmt.Sprintf(`
SELECT
  parent_sop.uuid AS package_sop_uuid,
  sp.product_final_price AS package_final_price,
  sp.tax_fee AS package_tax_fee, sp.service_fee AS package_service_fee,
  sp.service_tax AS package_service_tax, sp.free_num AS package_free_num,
  sp.give_num AS package_give_num, sp.product_num AS package_product_num,
  sp.refund_num AS package_refund_num, sp.product_price AS package_product_price,
  sp.product_package_uuid AS parent_product_package_uuid,
  CASE WHEN parent_pp.name IS NOT NULL AND parent_pp.name != '' THEN JSON_EXTRACT_SCALAR(parent_pp.name,'$.en') ELSE '' END AS parent_product_name,
  CASE WHEN parent_pc.name IS NOT NULL AND parent_pc.name != '' THEN JSON_EXTRACT_SCALAR(parent_pc.name,'$.en') ELSE '' END AS parent_category_name,
  CASE WHEN parent_ppc.name IS NOT NULL AND parent_ppc.name != '' THEN JSON_EXTRACT_SCALAR(parent_ppc.name,'$.en') ELSE '' END AS parent_category_parent_name,
  child_sop.product_package_uuid AS sub_product_package_uuid,
  child_sop.product_price AS sub_product_price, child_sop.sauce_price AS sub_sauce_price,
  COALESCE(sub_pp.price, 0) AS sub_latest_price,
  child_sop.num AS sub_num, child_sop.unit_num AS sub_unit_num, child_sop.copy_num AS sub_copy_num,
  JSON_EXTRACT_SCALAR(sub_pp.name,'$.en') AS sub_product_name,
  CASE WHEN sub_pb.name IS NOT NULL AND sub_pb.name != '' THEN JSON_EXTRACT_SCALAR(sub_pb.name,'$.en') ELSE '' END AS sub_flavor_name,
  JSON_EXTRACT_SCALAR(sub_pc.name,'$.en') AS sub_category_name,
  JSON_EXTRACT_SCALAR(sub_ppc.name,'$.en') AS sub_category_parent_name
FROM %s AS sp
JOIN %s AS parent_sop
  ON parent_sop.sale_order_uuid = sp.sale_order_uuid
 AND parent_sop.product_package_uuid = sp.product_package_uuid
 AND parent_sop.product_type = 1 AND parent_sop.delete_time = 0
JOIN %s AS child_sop
  ON child_sop.package_uuid = parent_sop.uuid
 AND child_sop.product_type = 2 AND child_sop.delete_time = 0
LEFT JOIN %s AS parent_pp  ON sp.product_package_uuid = parent_pp.uuid
LEFT JOIN %s AS parent_pc  ON parent_pp.category_uuid = parent_pc.uuid
LEFT JOIN %s AS parent_ppc ON parent_pc.parent_uuid = parent_ppc.uuid
LEFT JOIN %s AS sub_pp     ON child_sop.product_package_uuid = sub_pp.uuid
LEFT JOIN %s AS sub_sopb
  ON sub_sopb.sale_order_product_uuid = child_sop.uuid
 AND sub_sopb.is_flavor_bom = 1 AND sub_sopb.delete_time = 0
LEFT JOIN %s AS sub_pb     ON sub_sopb.product_bom_uuid = sub_pb.uuid
LEFT JOIN %s AS sub_pc     ON sub_pp.category_uuid = sub_pc.uuid
LEFT JOIN %s AS sub_ppc    ON sub_pc.parent_uuid = sub_ppc.uuid
WHERE sp.complete_time BETWEEN @start AND @end
  AND sp.product_type = 1%s
`,
		t("ttpos_statistics_product"),
		t("ttpos_sale_order_product"),
		t("ttpos_sale_order_product"),
		t("ttpos_product_package"),
		t("ttpos_product_category"),
		t("ttpos_product_category"),
		t("ttpos_product_package"),
		t("ttpos_sale_order_product_bom"),
		t("ttpos_product_bom"),
		t("ttpos_product_category"),
		t("ttpos_product_category"),
		notExists,
	)

	return sql, params
}

func (r *StatisticsBQRepo) cacheKey(companyUUID uint64, start, end int64, windows []BusinessStatusPeriod) string {
	var b strings.Builder
	fmt.Fprintf(&b, "pkgdetail:%d:%d:%d:w", companyUUID, start, end)
	for _, w := range windows {
		fmt.Fprintf(&b, ":%d-%d", w.StartTime, w.EndTime)
	}
	return b.String()
}

// ---------------------------------------------------------------------------
// 6. 服务层分叉(伪代码,接进去时写在 service/statistics.go)
// ---------------------------------------------------------------------------
//
// func (s *statisticsSrv) countProductPackageDetail(ctx, req, opts) []CountProductResp {
//     if s.bqConf.Enabled {
//         // 6.1 小表读窗口(MySQL,毫秒级,可再缓存)
//         var ws []BusinessStatusPeriod
//         ctx.GetDB().Raw("SELECT start_time, end_time FROM ttpos_business_status_period WHERE delete_time = 0").Scan(&ws)
//         // 6.2 走 BQ
//         rows, err := s.bqRepo.CountPackageDetailProductSaleBQ(stdCtx, ctx.GetDbId(), req.StartTime, req.EndTime, ws)
//         if err == nil {
//             return assembleResp(rows)   // 复用现有组装逻辑,把 row 映射成 CountProductResp
//         }
//         log.Warn("bq path failed, NOT falling back to mysql", "company_uuid", ctx.GetDbId(), "err", err)
//         return nil // 或返回缓存旧值 / 友好错误。事故期别 fallback MySQL。
//     }
//     // flag 关 → 原有 MySQL 实现,一行不改
//     statisticsRepo := repository.NewStatisticsRepo(ctx.GetDB())
//     detailData, _ := statisticsRepo.CountPackageDetailProductSale(repoReq, opts...)
//     return assembleResp(detailData)
// }
